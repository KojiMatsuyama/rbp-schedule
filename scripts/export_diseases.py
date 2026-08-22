#!/usr/bin/env python3
"""export_diseases.py — SQLite DB（data/stb.db の diseases テーブル）を
静的フロントエンド向けの data/diseases.js へ書き出す。

病害虫データの唯一無二の正は DB である（db_setup.py の DISEASES_SEED が
bootstrap のシード元）。本スクリプトはその正から「生成物」を再生成するだけ。
手作業で data/diseases.js を編集しないこと — 変更は DB 側で行い、
本スクリプトで再生成する。

用途:
  - Cloudflare Pages（静的・/api なし）など DB へのアクセス経路が無い環境で、
    index.html が <script src="data/diseases.js"> として初期 DISEASES を読むための
    スナップショットを生成する。
  - 実行サーバー（DGX）では /api/diseases が DB の正を返すため、本生成物は
    fetch 前に初期値として使われる。DB が変わったら push 前に再生成する。

使い方:
  python3 scripts/export_diseases.py          # data/stb.db → data/diseases.js
  python3 scripts/export_diseases.py --check  # 生成物とDBが一致しているか確認のみ
"""
import os
import sqlite3
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
OUT_PATH = os.path.join(APP_ROOT, "data", "diseases.js")

VECTOR_DIM = 10

HEADER = """// data/diseases.js — 病害虫定義（10次元ベクトル空間の次元定義）
// ⚠️ 生成物（自動生成・手編集禁止）。唯一無二の正は SQLite DB の diseases テーブル。
//    変更は DB 側で行い、`python3 scripts/export_diseases.py` で再生成すること。
//    bootstrap のシード元は db_setup.py の DISEASES_SEED。
// アプリケーション固有データ。framework/ 層には依存しない。
"""


def render(rows):
    """(id, name, type, icon) の行群を diseases.js の本文に整形する。"""
    lines = [HEADER.rstrip("\n")]
    # var を使うのは意図的: classic script のトップレベル const/let は
    # グローバル辞書環境（lexical）に束縛され、window.DISEASES の代入で
    # 上書きできない。var はグローバルオブジェクトのプロパティを作るため、
    # 実行サーバーが /api/diseases の DB 正で再代入（DISEASES = ...）でき、
    # 全スクリプトの裸参照 DISEASES がその値を見る。
    lines.append("var DISEASES = [")
    for i, (name, dtype, icon) in enumerate(rows):
        icon = icon if icon else ""
        lines.append(
            f"  {{ id: {i}, name: {json_str(name)}, type: {json_str(dtype)}, icon: {json_str(icon)} }},"
        )
    lines.append("];")
    lines.append("")
    return "\n".join(lines)


def json_str(value):
    """JS オブジェクト字面量の value を JS シングルクォート文字列として出力する。
    エスケープは JSON と同等（\\ と ' を逃がす）。"""
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


def read_db_rows():
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT name, type, icon FROM diseases ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return rows


def main():
    check_only = "--check" in sys.argv

    try:
        rows = read_db_rows()
    except sqlite3.Error as e:
        print(f"エラー: 病害虫DB を読めません（{DB_PATH}）。db_setup.py を実行してください。({e})", file=sys.stderr)
        sys.exit(1)

    if len(rows) != VECTOR_DIM:
        print(f"エラー: 病害虫DB が {len(rows)} 行（本来 {VECTOR_DIM} 行）", file=sys.stderr)
        sys.exit(1)

    content = render(rows)

    if check_only:
        existing = ""
        if os.path.exists(OUT_PATH):
            with open(OUT_PATH, "r", encoding="utf-8") as f:
                existing = f.read()
        if existing == content:
            print(f"OK: data/diseases.js は DB と一致しています。")
        else:
            print("不一致: data/diseases.js が DB と食い違っています。", file=sys.stderr)
            print("  → python3 scripts/export_diseases.py を実行して再生成してください。", file=sys.stderr)
            sys.exit(2)
        return

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"生成: {OUT_PATH}（{len(rows)} 行、DB 由来）")


if __name__ == "__main__":
    main()
