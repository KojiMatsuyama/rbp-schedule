#!/usr/bin/env python3
"""import_pesticides_master.py — 薬剤商品マスターの調査済み CSV を DB に取り込む。

 pesticides テーブルの 6 商品マスター列（brand / formulation / applicationRate /
 packSize / packUnit / price）を、外部調査で埋めた CSV から UPDATE する。

 正の所在: 薬剤データの唯一無一の正は data/stb.db の pesticides テーブル。本スクリプトは
   調査 CSV（緒言=物理界の製品データ）をその正に反映する「登録」であり、導出ではない。

 取り込みルール:
   - 行は id で一致させる（id 不一致は商品名でフォールバック、それでも無ければスキップ）。
   - 空セルは既存値を上書きしない（確定値のみ反映）。
   - 数値列（applicationRate/packSize/price）は数値に変換できなければスキップし警告。
   - 出典_備考に「要確認」「推定」を含む行は取り込むが、末尾に注意リストとして表示。

 使い方:
   python3 scripts/import_pesticides_master.py --dry-run          # 差分のみ表示・書かない
   python3 scripts/import_pesticides_master.py                    # 取り込み（既定: 薬剤ごとディレクトリ→単一CSV）
   python3 scripts/import_pesticides_master.py --csv 別.csv       # 任意の CSV を指定
   python3 scripts/import_pesticides_master.py --csv data/pesticides_調査  # 薬剤ごとディレクトリをまとめて読込
"""
import argparse
import csv
import os
import re
import sqlite3
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
DEFAULT_CSV = os.path.join(APP_ROOT, "data", "pesticides_調査済み.csv")
FALLBACK_CSV = os.path.join(APP_ROOT, "data", "pesticides_調査用.csv")
PER_PEST_DIR = os.path.join(APP_ROOT, "data", "pesticides_調査")

# CSV 列名 → (DB列, 種別)。種別: text / real
COLMAP = [
    # 散布量（applicationRate）は薬剤の商品仕様ではなく、対象病害虫・処方側の
    # 使用量のため、薬剤マスターには取り込まない（ユーザー判断 2026-09-08）。
    # 出典_備考（URL・採用品メモ）も接地の証左だが、薬剤マスターの列としては
    # 破棄する（ユーザー判断 2026-09-08）。
    ("剤形(既存/調査)",     "formulation",     "text"),
    ("メーカー(調査)",      "brand",           "text"),
    ("梱包形態(調査)",      "packaging",       "text"),
    ("内容の形(調査)",      "contentForm",     "text"),
    ("容量_1個(調査)",      "capacity",        "real"),
    ("販売単位_何個(調査)", "packSize",        "real"),
    ("容量単位(調査)",      "packUnit",        "text"),
    ("参考価格_円(調査)",   "price",           "real"),
]
NOTE_COL = "出典_備考(調査メモ)"
UNCERTAIN = re.compile(r"要確認|推定|不明|未確認")


def to_real(raw):
    """'25,000' / '0.5' / '' → float or None。数値に変換できなければ None。"""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    m = re.match(r"^[+-]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def resolve_csv(path):
    """--csv 指定時はそのまま。未指定時は 薬剤ごとディレクトリ→単一CSV の順。

    薬剤ごとディレクトリ（data/pesticides_調査/）が現在の正規の入力であるため優先し、
    単一CSV（調査済み/調査用）は後方互換のフォールバック。"""
    if path:
        return path
    for cand in (PER_PEST_DIR, DEFAULT_CSV, FALLBACK_CSV):
        if os.path.exists(cand):
            return cand
    return PER_PEST_DIR


def read_rows(path):
    """CSV 1 文件或は CSV 群のディレクトリから行リストを返す。"""
    if os.path.isdir(path):
        rows = []
        for fn in sorted(os.listdir(path)):
            if not fn.lower().endswith(".csv"):
                continue
            with open(os.path.join(path, fn), newline="", encoding="utf-8-sig") as f:
                rows.extend(csv.DictReader(f))
        return rows
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description="薬剤商品マスター CSV → DB 取り込み")
    ap.add_argument("--csv", help="取り込む CSV のパス（既定: 調査済み→調査用）")
    ap.add_argument("--dry-run", action="store_true", help="差分のみ表示・DBを書かない")
    args = ap.parse_args()

    csv_path = resolve_csv(args.csv)
    if not os.path.exists(csv_path):
        print(f"エラー: CSV が見つかりません: {csv_path}", file=sys.stderr)
        sys.exit(1)

    rows = read_rows(csv_path)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # id → row / 商品名 → row を事前取得（フォールバック用）
    db_by_id = {r["id"]: r for r in conn.execute("SELECT * FROM pesticides")}
    db_by_name = {r["name"]: r for r in conn.execute("SELECT * FROM pesticides")}

    updated, skipped, uncertain = 0, 0, []
    for row in rows:
        cid = (row.get("id") or "").strip()
        name = (row.get("商品名") or "").strip()
        note = (row.get(NOTE_COL) or "").strip()

        dbrow = db_by_id.get(cid) or db_by_name.get(name)
        if dbrow is None:
            skipped += 1
            print(f"  [スキップ] 未登録: id={cid or '-'} 商品名={name or '-'}")
            continue

        sets, vals = [], []
        for col, dbcol, kind in COLMAP:
            raw = (row.get(col) or "").strip()
            if not raw:
                continue  # 空セルは上書きしない
            if kind == "real":
                val = to_real(raw)
                if val is None:
                    print(f"  [警告] {dbrow['id']} {dbcol}={raw!r} は数値に変換できずスキップ")
                    continue
            else:
                val = raw
            if dbrow[dbcol] == val:
                continue  # 変化なし
            sets.append(f"{dbcol} = ?")
            vals.append(val)

        if not sets:
            continue

        if not args.dry_run:
            vals.append(dbrow["id"])
            conn.execute(f"UPDATE pesticides SET {', '.join(sets)} WHERE id = ?", vals)
        updated += 1
        changed = ", ".join(f"{s.split(' =')[0]}={v!r}" for s, v in zip(sets, vals))
        flag = " [要確認]" if UNCERTAIN.search(note) else ""
        print(f"  [{'予定' if args.dry_run else '更新'}] {dbrow['id']} {dbrow['name']}: {changed}{flag}")
        if UNCERTAIN.search(note):
            uncertain.append(f"{dbrow['id']} {dbrow['name']} — {note}")

    if not args.dry_run:
        conn.commit()
    conn.close()

    kind = "ディレクトリ" if os.path.isdir(csv_path) else "CSV"
    print(f"\n{'[dry-run] ' if args.dry_run else ''}{kind}: {csv_path}")
    print(f"  更新 {updated} 件 / スキップ {skipped} 件"
          + ("（DB は未変更）" if args.dry_run else "（コミット済み）"))
    if uncertain:
        print(f"\n  ⚠ 要確認/推定 {len(uncertain)} 件（取り込み済み・再確認推奨）:")
        for u in uncertain:
            print(f"    - {u}")


if __name__ == "__main__":
    main()
