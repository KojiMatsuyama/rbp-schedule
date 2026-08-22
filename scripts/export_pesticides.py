#!/usr/bin/env python3
"""export_pesticides.py — SQLite DB（data/stb.db の pesticides テーブル）から
静的フロントエンド向け data/pesticides.js と Python/Haskell RBP 用 data/pesticides.json
を再生成する。

薬剤データの唯一無一の正は DB である（薬剤マスターUI / server.py の /api/pesticides
CRUD が編集元）。本スクリプトはその正から「生成物」を再生成するだけ。
手作業で data/pesticides.js / data/pesticides.json を編集しないこと —
変更は DB 側で行い、本スクリプトで再生成する。

用途:
  - data/pesticides.js → ブラウザ内 JS RBP エンジンの PESTICIDE_DB（静的スナップショット。
    実行サーバーでは loadPesticides() が /api/pesticides の DB 正で上書きし、
    行列定数を再構築する）。
  - data/pesticides.json → Python/Haskell RBP エンジン
    （rbp-algebra-python/data_loader.py, rbp-algebra/src/Data/RBP/DataLoader.hs）と
    bootstrap（空DBをシード）のデータ源。Haskell は SQLite を直接読みにくいため、
    json は削除せず「生成物」として管理する（diseases とはここだけ異なる）。

maxApplications（無制限）の DB 値 'inf' の表現:
  - JSON では文字列 "inf"（Python data_loader が 'inf' → -1 に変換）
  - JS  では Infinity（エンジンが maxApplications === Infinity で無制限判定）

使い方:
  python3 scripts/export_pesticides.py          # DB → js + json を再生成
  python3 scripts/export_pesticides.py --check  # 生成物とDBが一致するか確認のみ
"""
import json
import os
import sqlite3
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
JS_PATH = os.path.join(APP_ROOT, "data", "pesticides.js")
JSON_PATH = os.path.join(APP_ROOT, "data", "pesticides.json")

# JSON/JS 両方で使う正規フィールド順（既存 pesticides.json と一致）
FIELDS = [
    "id", "name", "activeIngredient", "category",
    "targetVector", "targetNames",
    "phiDays", "mixingRestriction", "mixingBanTargets",
    "maxApplications", "toxicityClass", "system", "systemCode", "dilutionRate",
]

CATEGORY_LABEL = {
    "fungicide": "殺菌剤（病害）",
    "insecticide": "殺虫剤（害虫）",
    "acaricide": "殺ダニ剤（害虫）",
}

JS_HEADER = """// data/pesticides.js — 薬剤仕様データベース（PESTICIDE_DB）
// ⚠️ 生成物（自動生成・手編集禁止）。唯一無一の正は SQLite DB の pesticides テーブル。
//    変更は DB 側（薬剤マスターUI / /api/pesticides）で行い、
//    `python3 scripts/export_pesticides.py` で再生成すること。
// 10次元ベクトル空間の次元定義の正は DB の diseases テーブル（data/diseases.js はその生成物）。
// インデックス: 0:炭疽病 1:灰色かび病 2:うどんこ病 3:ナミハダニ 4:ハスモンヨトウ
//              5:オオタバコガ 6:ミカンキイロアザミウマ 7:ワタアブラムシ 8:アブラムシ 9:コナジラミ
"""


# --- DB 読み込み ---

def read_rows():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM pesticides ORDER BY id").fetchall()]
    conn.close()
    for r in rows:
        for col in ("targetVector", "targetNames", "mixingBanTargets"):
            if isinstance(r.get(col), str):
                try:
                    r[col] = json.loads(r[col])
                except (json.JSONDecodeError, TypeError):
                    pass
    return rows


# --- JS 生成 ---

def js_str(value):
    """JS シングルクォート文字列（\\ と ' と改行を逃がす）。"""
    out = []
    for ch in str(value):
        if ch == "\\":
            out.append("\\\\")
        elif ch == "'":
            out.append("\\'")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "'" + "".join(out) + "'"


def js_num(value):
    """数値を JS リテラルに。整数値なら .0 を落として int 風にする（既存スタイルに合わせる）。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def js_array_int(items):
    return "[" + ",".join(str(int(x)) for x in items) + "]"


def js_array_str(items):
    return "[" + ",".join(js_str(x) for x in items) + "]"


def js_value(field, value):
    if value is None:
        return "null"
    if field in ("targetVector",):
        return js_array_int(value)
    if field in ("targetNames", "mixingBanTargets"):
        return js_array_str(value)
    if field == "maxApplications":
        if value == "inf":
            return "Infinity"
        return js_num(value)
    if field in ("phiDays",):
        return js_num(value)
    # 文字列系
    return js_str(value)


def build_js(rows):
    lines = [JS_HEADER.rstrip("\n"), "var PESTICIDE_DB = ["]
    prev_cat = None
    for r in rows:
        cat = r.get("category")
        if cat != prev_cat:
            lines.append(f"  // ── {CATEGORY_LABEL.get(cat, cat)} ──")
            prev_cat = cat
        parts = [f"  {{ id: {js_str(r['id'])}"]
        for f in FIELDS[1:]:
            parts.append(f" {f}: {js_value(f, r.get(f))}")
        lines.append(", ".join(parts) + " },")
    lines.append("];")
    lines.append("")
    return "\n".join(lines)


# --- JSON 生成 ---

def build_json(rows):
    out = []
    for r in rows:
        obj = {}
        for f in FIELDS:
            v = r.get(f)
            # JSON は DB の生値を保持（'inf' はそのまま文字列、phiDays は float）
            if f in ("phiDays", "maxApplications") and isinstance(v, float):
                obj[f] = v
            else:
                obj[f] = v
        out.append(obj)
    # 既存フォーマット（2スペース・アレーは展開）に合わせる
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def main():
    check_only = "--check" in sys.argv

    try:
        rows = read_rows()
    except sqlite3.Error as e:
        print(f"エラー: 薬剤DB を読めません（{DB_PATH}）。({e})", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("エラー: 薬剤DB が空です。db_setup.py を実行してください。", file=sys.stderr)
        sys.exit(1)

    js_content = build_js(rows)
    json_content = build_json(rows)

    if check_only:
        import re
        ok = True
        for path, content in ((JS_PATH, js_content), (JSON_PATH, json_content)):
            if not os.path.exists(path):
                print(f"  欠落: {path}", file=sys.stderr)
                ok = False
                continue
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
            if existing == content:
                print(f"OK: {os.path.basename(path)} は DB と一致しています。")
            else:
                print(f"不一致: {os.path.basename(path)} が DB と食い違っています。", file=sys.stderr)
                ok = False
        if not ok:
            print("  → python3 scripts/export_pesticides.py を実行して再生成してください。", file=sys.stderr)
            sys.exit(2)
        return

    with open(JS_PATH, "w", encoding="utf-8") as f:
        f.write(js_content)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        f.write(json_content)
    print(f"生成: {JS_PATH}（{len(rows)} 剤）")
    print(f"生成: {JSON_PATH}（{len(rows)} 剤）")


if __name__ == "__main__":
    main()
