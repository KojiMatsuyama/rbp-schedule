#!/usr/bin/env python3
"""防除カレンダー.txt → spray_schedule テーブルへの取り込み。

「日付(M/D) / 病害虫一覧 / → セットN」の3行1組を読み、指定年度で
spray_schedule に INSERT する。

- 月ヘッダ（`# 📅 **1月**`、`-2月` 等）・説明文は無視（日付行自体に月/日が含まれるため）
- 同一 schedule_date が既に存在する行はスキップ（idempotent / 再取り込み安全）
- set_ids には ["セットN"]、notes には病害虫一覧を格納
- eval_box_id は NULL（DB の eval_boxes は EB-NN 体系で BOX-NN と別、RBP は vector のみ必要）
- 処方生成（rb_out_json / pesticide_ids）は別スクリプト rx_prescribe.py が担当

使い方:
    python3 scripts/import_calendar.py            # 既定: 2026年
    python3 scripts/import_calendar.py 2027       # 2027年分
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# DDL の DEFAULT は datetime('now','jst') だが、jst モディファイアは使えず空を返すため、
# 明示的に JST を計算して渡す（server.py の INSERT ハンドラと同じ方針）
JST = timezone(timedelta(hours=9))

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
CAL_PATH = os.path.join(APP_ROOT, "防除カレンダー.txt")
SOURCE_TAG = "防除カレンダー.txt"

DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})\s*$")
SET_RE = re.compile(r"セット(\d+)")


def parse_calendar(path):
    """txt を [{month, day, pests, set}, ...] にパースする。"""
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f]

    entries = []
    n = len(lines)
    i = 0
    while i < n:
        m = DATE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        month, day = int(m.group(1)), int(m.group(2))

        # 病害虫行: 次にある「空行でも日付でもセットでもない」行
        pests_line = None
        j = i + 1
        while j < n:
            if lines[j] == "":
                j += 1
                continue
            if DATE_RE.match(lines[j]) or SET_RE.search(lines[j]):
                break
            pests_line = lines[j]
            break

        # セット行: 日付行の直後にある最初の「セットN」行
        set_num = None
        for k in range(i + 1, n):
            sm = SET_RE.search(lines[k])
            if sm:
                set_num = int(sm.group(1))
                break

        pests = []
        if pests_line:
            pests = [p.strip() for p in re.split(r"[、,，]", pests_line) if p.strip()]

        entries.append({"month": month, "day": day, "pests": pests, "set": set_num})
        i += 1
    return entries


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    if not os.path.isfile(CAL_PATH):
        print(f"ERROR: カレンダーファイルが見つかりません: {CAL_PATH}")
        sys.exit(1)

    entries = parse_calendar(CAL_PATH)
    print(f"パース結果: {len(entries)} 件（{year}年分として登録）")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    inserted = 0
    skipped = 0
    bad = 0
    for e in entries:
        schedule_date = f"{year}-{e['month']:02d}-{e['day']:02d}"
        exists = conn.execute(
            "SELECT 1 FROM spray_schedule WHERE schedule_date = ?", (schedule_date,)
        ).fetchone()
        if exists:
            skipped += 1
            continue
        if e["set"] is None:
            bad += 1
            print(f"  !! セット番号不明のためスキップ: {schedule_date}")
            continue
        set_ids = json.dumps([f"セット{e['set']}"], ensure_ascii=False)
        notes = "、".join(e["pests"])
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO spray_schedule
               (schedule_date, status, trigger_type, trigger_ref,
                eval_box_id, rb_out_json, set_ids, pesticide_ids, notes,
                created_at, updated_at)
               VALUES (?, 'scheduled', 'cycle', ?, NULL, NULL, ?, '[]', ?, ?, ?)""",
            (schedule_date, SOURCE_TAG, set_ids, notes, now, now),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"完了: 挿入 {inserted} 件 / スキップ(既存) {skipped} 件 / 不備 {bad} 件")


if __name__ == "__main__":
    main()
