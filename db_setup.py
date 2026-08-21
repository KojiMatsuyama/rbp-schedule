#!/usr/bin/env python3
"""db_setup.py — SQLite DBの作成・初期データ投入スクリプト。
既存のJSONファイルからデータを抽出してSQLiteに格納する。
"""
import json
import os
import sqlite3

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pesticides (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    activeIngredient TEXT,
    category TEXT,
    targetVector TEXT,       -- JSON array of ints
    targetNames TEXT,        -- JSON array of strings
    phiDays REAL,
    mixingRestriction TEXT,
    mixingBanTargets TEXT,   -- JSON array of strings
    maxApplications REAL,
    toxicityClass TEXT,
    system TEXT,
    systemCode TEXT
);

CREATE TABLE IF NOT EXISTS diseases (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('disease', 'pest'))
);

CREATE TABLE IF NOT EXISTS eval_boxes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vector TEXT NOT NULL     -- JSON array of ints
);

CREATE TABLE IF NOT EXISTS eval_boxes_custom (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vector TEXT NOT NULL     -- JSON array of ints
);

CREATE TABLE IF NOT EXISTS spray_history (
    date TEXT PRIMARY KEY,
    pests TEXT NOT NULL,     -- JSON array of strings
    vector TEXT NOT NULL     -- JSON array of ints
);

CREATE TABLE IF NOT EXISTS spray_schedule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_date   TEXT    NOT NULL,          -- 予定日 YYYY-MM-DD
    actual_date     TEXT,                      -- 実際の実施日（NULL=未実施）
    status          TEXT    NOT NULL DEFAULT 'scheduled'
                        CHECK(status IN ('scheduled', 'done', 'missed', 'rescheduled')),
    trigger_type    TEXT    NOT NULL DEFAULT 'cycle'
                        CHECK(trigger_type IN ('cycle', 'observation', 'forecast')),
    trigger_ref     TEXT,                      -- 参照元ID（EVAL_BOX IDなど）
    eval_box_id     TEXT REFERENCES eval_boxes(id),
    rb_out_json     TEXT,                      -- RBP_OUTのJSON（要求評価+仕様決定の結果）
    set_ids         TEXT    NOT NULL,          -- JSON: ["セット1", "セット7"]
    pesticide_ids   TEXT    NOT NULL,          -- JSON: ["P40", "P42"]
    operator        TEXT,                      -- 担当者
    weather         TEXT,                      -- 天候（晴/曇/雨）
    notes           TEXT,                      -- 備考
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst'))
);

CREATE INDEX IF NOT EXISTS idx_spray_schedule_date ON spray_schedule(schedule_date);
CREATE INDEX IF NOT EXISTS idx_spray_schedule_status ON spray_schedule(status);
CREATE INDEX IF NOT EXISTS idx_spray_schedule_eval_box ON spray_schedule(eval_box_id);
"""


def seed_from_json(conn):
    """既存のJSONファイルからデータを投入（初回のみ）."""
    cur = conn.cursor()

    # diseases.json
    path = os.path.join(APP_ROOT, "data", "diseases.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            diseases = json.load(f)
        for d in diseases:
            cur.execute(
                "INSERT OR IGNORE INTO diseases (id, name, type) VALUES (?, ?, ?)",
                (d["id"], d["name"], d["type"]),
            )

    # pesticides.json
    path = os.path.join(APP_ROOT, "data", "pesticides.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            pesticides = json.load(f)
        for p in pesticides:
            cur.execute(
                """INSERT OR IGNORE INTO pesticides
                   (id, name, activeIngredient, category, targetVector, targetNames,
                    phiDays, mixingRestriction, mixingBanTargets, maxApplications,
                    toxicityClass, system, systemCode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    p["id"],
                    p["name"],
                    p.get("activeIngredient"),
                    p.get("category"),
                    json.dumps(p.get("targetVector", [])),
                    json.dumps(p.get("targetNames", [])),
                    p.get("phiDays"),
                    p.get("mixingRestriction"),
                    json.dumps(p.get("mixingBanTargets", [])),
                    p.get("maxApplications"),
                    p.get("toxicityClass"),
                    p.get("system"),
                    p.get("systemCode"),
                ),
            )

    # eval_boxes.json
    path = os.path.join(APP_ROOT, "data", "eval_boxes.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            eval_boxes = json.load(f)
        for eid, eb in eval_boxes.items():
            cur.execute(
                "INSERT OR IGNORE INTO eval_boxes (id, name, vector) VALUES (?, ?, ?)",
                (eid, eb["name"], json.dumps(eb["vector"])),
            )

    # eval_boxes_custom.json
    path = os.path.join(APP_ROOT, "data", "eval_boxes_custom.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            custom = json.load(f)
        for cid, cb in custom.items():
            cur.execute(
                "INSERT OR IGNORE INTO eval_boxes_custom (id, name, vector) VALUES (?, ?, ?)",
                (cid, cb["name"], json.dumps(cb["vector"])),
            )

    conn.commit()


def main():
    os.makedirs(os.path.join(APP_ROOT, "data"), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(CREATE_SQL)
    seed_from_json(conn)

    # Counts
    for table in ("pesticides", "diseases", "eval_boxes", "eval_boxes_custom", "spray_history"):
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"  {table}: {count} rows")

    conn.close()
    print(f"\nDB created: {DB_PATH}")


if __name__ == "__main__":
    main()
