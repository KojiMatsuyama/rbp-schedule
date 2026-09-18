#!/usr/bin/env python3
"""export_pesticide_investigation.py — 薬剤ごとに調査用CSVを生成する。

 pesticides テーブルの各薬剤について、外部AIに渡して商品マスター（メーカー・剤形・
 散布量・販売単位・単位・価格）を調査させるための CSV を1件ずつ生成する。

 生成物は data/pesticides_調査/ に {id}_{商品名}.csv として書き出す。各CSVは
 識別情報（id/商品名/有効成分/分類/希釈倍率）を埋め、調査すべき6列は既存値のみ残し
 空欄にする。列構成は import_pesticides_master.py と同一なので、調査済みCSVを
 戻しディレクトリに置けば取込スクリプトがそのまま読める。

 安全性: 出典_備考(調査メモ) が埋まったCSV（= 外部AIが調査済みとみなす）は
 上書きせずスキップする（--force で上書き）。DB由来の既存値（剤形・P01のbrand等）
 は出典_備考に触れないため、誤って「調査済み」とは判定されない。

 使い方:
   python3 scripts/export_pesticide_investigation.py              # 全薬剤を生成
   python3 scripts/export_pesticide_investigation.py --id P01     # 指定1件のみ
   python3 scripts/export_pesticide_investigation.py --force      # 既存を上書き
   python3 scripts/export_pesticide_investigation.py --out DIR    # 出力先を指定
"""
import argparse
import csv
import os
import re
import sqlite3
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
DEFAULT_OUT = os.path.join(APP_ROOT, "data", "pesticides_調査")

HEADERS = [
    "id", "商品名", "有効成分", "分類", "希釈倍率(既存)",
    "剤形(既存/調査)", "メーカー(調査)", "梱包形態(調査)", "内容の形(調査)",
    "散布量L_10a(調査)", "容量_1個(調査)", "販売単位_何個(調査)",
    "容量単位(調査)", "参考価格_円(調査)", "出典_備考(調査メモ)",
]
NOTE_COL = "出典_備考(調査メモ)"


def safe_name(name):
    """ファイル名に不適切な文字を _ に置換。"""
    return re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip()) or "unnamed"


def is_investigated(path):
    """出典_備考が埋まっていれば調査済み（外部AIが書いた）とみなす。"""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if (row.get(NOTE_COL) or "").strip():
                    return True
    except (OSError, csv.Error):
        return False
    return False


def main():
    ap = argparse.ArgumentParser(description="薬剤ごとに調査用CSVを生成")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力ディレクトリ")
    ap.add_argument("--id", dest="pid", help="生成する薬剤id（例: P01）。省略=全件")
    ap.add_argument("--force", action="store_true", help="調査済みのCSVも上書き")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    q = ("SELECT id, name, activeIngredient, category, dilutionRate, "
         "formulation, brand, packaging, contentForm, applicationRate, capacity, "
         "packSize, packUnit, price "
         "FROM pesticides")
    if args.pid:
        q += " WHERE id = ?"
        rows = conn.execute(q, (args.pid,)).fetchall()
    else:
        rows = conn.execute(q + " ORDER BY id").fetchall()
    conn.close()

    if not rows:
        print(f"エラー: 薬剤が見つかりません（id={args.pid or '全件'}）", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.out, exist_ok=True)
    made, skipped = 0, 0
    for (pid, name, ai, cat, dil, form, brand, packaging, contentform,
         arate, capacity, psize, punit, price) in rows:
        fn = os.path.join(args.out, f"{pid}_{safe_name(name)}.csv")
        if os.path.exists(fn) and not args.force and is_investigated(fn):
            skipped += 1
            print(f"  [スキップ] 調査済み: {os.path.basename(fn)}")
            continue
        data = [
            pid, name or "", ai or "", cat or "", dil or "",
            form or "", brand or "", packaging or "", contentform or "",
            arate if arate is not None else "",
            capacity if capacity is not None else "",
            psize if psize is not None else "",
            punit or "", price if price is not None else "",
            "",
        ]
        with open(fn, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(HEADERS)
            w.writerow(data)
        made += 1
        print(f"  [生成] {os.path.basename(fn)}")

    print(f"\n生成 {made} 件 / スキップ {skipped} 件 → {args.out}")
    print("外部AIに渡す際は pesticides_調査指示.md のルールを併せて渡してください。")
    print("調査済みCSVを戻したら: python3 scripts/import_pesticides_master.py --csv <調査済みCSV>")


if __name__ == "__main__":
    main()
