#!/usr/bin/env python3
"""
scripts/import_pesticides_remaining.py — 残課題調査CSV → DB 反映

data/pesticides_残課題調査.csv を読み、外部AIが埋めた値を pesticides テーブルへ反映する。
2グループを扱う:
  A群(分類矛盾): 正しい分類 → category、正しい有効成分(再確認) → activeIngredient
  B群(特定不能): 正式な製品名/成分/分類/メーカー/剤形/梱包/内容の形/容量/販売単位/価格

使い方:
  .venv/bin/python scripts/import_pesticides_remaining.py --dry-run   # 差分のみ表示
  .venv/bin/python scripts/import_pesticides_remaining.py             # 適用
  .venv/bin/python scripts/import_pesticides_remaining.py --csv <file>
"""
import argparse, csv, os, sqlite3, sys

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stb.db")
DEFAULT_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pesticides_残課題調査.csv")

# 正しい分類(日本語) → DB category
CATMAP = {
    "殺菌剤": "fungicide",
    "殺虫剤": "insecticide",
    "殺ダニ剤": "acaricide",
    "殺線虫剤": "nematicide",
    "除草剤": "herbicide",
    "殺菌剤・殺虫剤": "fungicide",   # 複合は先頭で
}

def detect_encoding(path):
    """外部AIが保存したCSVは Shift-JIS/CP932 の可能性がある → 自動検出"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8-sig"

def to_real(s):
    if s is None: return None
    s = str(s).strip().replace(",", "").replace("円", "").replace(" ", "")
    if s in ("", "要確認", "不明", "-", "—"): return None
    import re
    m = re.match(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None

def clean(s):
    """空・要確認・不明・(特定) 等を None に"""
    if s is None: return None
    s = str(s).strip()
    if s in ("", "要確認", "不明", "未確認", "-", "—", "(特定)", "(要確認)", "特定不能"): return None
    return s

def clean_formulation(s):
    """剤形はRBP行列のキー（制御語彙）→ 括弧内注記を落として純化
    例: 'フロアブル（水和剤）' → 'フロアブル'"""
    import re
    v = clean(s)
    if v is None: return None
    v = re.sub(r"[（(][^（）()]*[）)]", "", v).strip()
    return v or None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"CSV が見つかりません: {args.csv}")
    enc = detect_encoding(args.csv)
    print(f"[encoding] {enc}")
    rows = list(csv.DictReader(open(args.csv, encoding=enc)))

    conn = sqlite3.connect(DB)
    plan = []  # (id, [(col, old, new), ...])
    for r in rows:
        pid = (r.get("id") or "").strip()
        if not pid: continue
        cur = conn.execute("SELECT * FROM pesticides WHERE id=?", (pid,)).fetchone()
        if cur is None:
            print(f"  [skip] {pid}: DB に存在しません")
            continue
        cols = {d[0]: cur[i] for i, d in enumerate(conn.execute("SELECT * FROM pesticides").description)}

        changes = []
        def upd(col, newval):
            old = cols.get(col)
            if newval is None: return
            if str(old or "") == str(newval): return
            changes.append((col, old, newval))

        # A群: 分類（ヘッダーは○なし版・○あり版の両方に対応）
        cat_ja = clean(r.get("正しい分類(殺菌剤/殺虫剤/殺ダニ剤)")
                       or r.get("正しい分類(○殺菌剤/○殺虫剤/○殺ダニ剤)"))
        if cat_ja:
            upd("category", CATMAP.get(cat_ja, cat_ja))
        # 有効成分（A/B共通・再確認値）
        upd("activeIngredient", clean(r.get("正しい有効成分(再確認)")))
        # B群: 製品名・メーカー・剤形・梱包・内容の形・容量・販売単位・価格
        upd("name", clean(r.get("正式な製品名(剤形付き)")))
        upd("brand", clean(r.get("メーカー")))
        upd("formulation", clean_formulation(r.get("剤形(再確認)")))
        upd("packaging", clean(r.get("梱包形態(袋/ボトル/缶/箱)")))
        upd("contentForm", clean(r.get("内容の形(液体/粉/顆粒/タブレット/粒/ペレット)")))
        upd("capacity", to_real(r.get("容量_1個")))
        upd("packSize", to_real(r.get("販売単位_何個")))
        upd("packUnit", clean(r.get("容量単位(kg/g/L/mL)")))
        upd("price", to_real(r.get("参考価格_円")))

        if changes:
            plan.append((pid, r.get("商品名(DB)", ""), changes))

    # 表示
    print(f"=== {'DRY-RUN' if args.dry_run else 'APPLY'}: {len(plan)} 件変更予定 ===")
    for pid, name, changes in plan:
        print(f"\n  {pid} {name}:")
        for col, old, new in changes:
            print(f"    {col}: {old!r} → {new!r}")
    if not plan:
        print("  （変更なし）")
        conn.close()
        return

    if args.dry_run:
        print("\n[dry-run] 適用していません。--dry-run なしで実行すると適用されます。")
        conn.close()
        return

    for pid, name, changes in plan:
        sets = ", ".join(f"{c}=?" for c, _, _ in changes)
        vals = [n for _, _, n in changes] + [pid]
        conn.execute(f"UPDATE pesticides SET {sets} WHERE id=?", vals)
    conn.commit()
    print(f"\n適用完了: {len(plan)} 件")
    conn.close()

if __name__ == "__main__":
    main()
