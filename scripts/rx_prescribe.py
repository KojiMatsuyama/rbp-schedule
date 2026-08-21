#!/usr/bin/env python3
"""防除暦（spray_schedule）の処方自動生成 — cron 本体。

毎日朝に cron から実行される。対象は:
    status='scheduled' かつ rb_out_json IS NULL かつ
    today <= schedule_date <= today + RX_LEAD_DAYS
の行。各行について:
  1. set_ids の「セットN」→ data/eval_boxes.json の BOX-NN.vector（10次元0/1）
  2. rbp-algebra-python の api.prescribe(vector) で薬剤セットを算出
  3. 薬剤 id → data/pesticides.json の dilutionRate（希釈率）で補完
  4. spray_schedule を更新:
       - pesticide_ids = 薬剤名配列（UI が素通し表示するため）
       - rb_out_json   = 構造化結果（id+用量+スコア+代替数）
  5. Slack 通知（chat_client.send_message。未設定なら DB 保存のみ）

先回し日数は .env の RX_LEAD_DAYS（既定 3）。環境変数で上書き可:
    RX_LEAD_DAYS=120 python3 scripts/rx_prescribe.py   # テスト用に広く
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
ENV_PATH = os.path.join(APP_ROOT, ".env")
EVAL_BOXES_JSON = os.path.join(APP_ROOT, "data", "eval_boxes.json")
PESTICIDES_JSON = os.path.join(APP_ROOT, "data", "pesticides.json")
LOG_DIR = os.path.join(APP_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "rx_prescribe.log")

JST = timezone(timedelta(hours=9))
SET_RE = re.compile(r"セット(\d+)")

sys.path.insert(0, APP_ROOT)  # chat_client / RBP api の import 用


def load_env():
    """簡易 .env パーサ（chat_client と同じ形式）。"""
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def get_lead_days():
    """先回し日数。環境変数 > .env > 既定(3)。"""
    raw = os.environ.get("RX_LEAD_DAYS") or load_env().get("RX_LEAD_DAYS") or "3"
    try:
        return int(raw)
    except ValueError:
        return 3


def load_eval_box_vectors():
    """{BOX-NN: {'vector': [...], 'diseases': [...]}} を返す。"""
    with open(EVAL_BOXES_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items()}


def load_pesticide_meta():
    """{P01: {'name': ..., 'dilutionRate': ...}} を返す。"""
    with open(PESTICIDES_JSON, encoding="utf-8") as f:
        rows = json.load(f)
    return {
        r["id"]: {"name": r.get("name"), "dilutionRate": r.get("dilutionRate")}
        for r in rows
    }


def run_rbp(vector):
    """RBP Python エンジンを呼び出す（mcp_tools._run_rbp_and_enrich と同じパターン）。"""
    sys.path.insert(0, os.path.join(APP_ROOT, "rbp-algebra-python"))
    try:
        import api as py_api
        return py_api.prescribe(vector)
    except Exception as e:
        return {"error": f"RBPエンジンエラー: {e}"}
    finally:
        sys.path.pop(0)


def enrich_pesticides(pests, meta):
    """RBP の pesticides([{id,name,system}]) に dilutionRate を付け足す。"""
    out = []
    for p in pests or []:
        pid = p.get("id")
        m = meta.get(pid, {})
        out.append({
            "id": pid,
            "name": p.get("name") or m.get("name"),
            "system": p.get("system"),
            "dilutionRate": m.get("dilutionRate"),
        })
    return out


def build_slack_text(row, set_label, rbp, best_pests, alt_count):
    lines = [f"📅 {row['schedule_date']} 防除予定（{set_label}）"]
    if row["notes"]:
        lines.append(f"🐛 {row['notes']}")
    if best_pests:
        lines.append("💊 処方:")
        for p in best_pests:
            dose = f"（{p['dilutionRate']}）" if p.get("dilutionRate") else ""
            lines.append(f"   ・{p['name'] or p.get('id')}{dose}")
    else:
        lines.append("💊 処方: 該当薬剤なし")
    score = (rbp.get("best") or {}).get("totalScore")
    lines.append(f"📊 スコア {score if score is not None else '-'} / 代替 {alt_count}案")
    return "\n".join(lines)


def process_row(row, now, box_vectors, pesticide_meta, conn):
    """1行分の処方生成（RBP実行→DB更新→Slack通知）を行う。

    戻り値 dict:
      ok: bool            — 生成完了したか
      error: str          — 失敗時の理由（ok=False のとき）
      set_label: str      — 「セットN」
      names: [str]        — 処方された薬剤名
      slack_ok: bool      — Slack送信成功したか
    """
    set_ids = json.loads(row["set_ids"]) if row["set_ids"] else []
    set_num = None
    for s in set_ids:
        m = SET_RE.search(str(s))
        if m:
            set_num = int(m.group(1))
            break
    set_label = f"セット{set_num}" if set_num else ""

    box_key = f"BOX-{set_num:02d}" if set_num else None
    box = box_vectors.get(box_key) if box_key else None
    if box is None:
        return {"ok": False, "error": f"BOX 未対応（{set_label or 'セット未設定'}）",
                "set_label": set_label, "names": [], "slack_ok": False}

    rbp = run_rbp(box["vector"])
    if isinstance(rbp, dict) and rbp.get("error"):
        return {"ok": False, "error": rbp["error"],
                "set_label": set_label, "names": [], "slack_ok": False}

    best = rbp.get("best") or {}
    best_pests = enrich_pesticides(best.get("pesticides"), pesticide_meta)
    alt_count = len(rbp.get("alternatives") or [])
    names = [p["name"] for p in best_pests if p.get("name")]

    rb_out = {
        "set": set_num,
        "setLabel": set_label,
        "box": box_key,
        "vector": box["vector"],
        "rbp_status": rbp.get("status"),
        "best": {
            "pesticides": best_pests,
            "totalScore": best.get("totalScore"),
            "matchCount": best.get("matchCount"),
            "breakdown": best.get("breakdown"),
        },
        "alternatives_count": alt_count,
        "generated_at": now.isoformat(),
    }

    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """UPDATE spray_schedule
           SET rb_out_json=?, pesticide_ids=?, updated_at=?
           WHERE id=?""",
        (
            json.dumps(rb_out, ensure_ascii=False),
            json.dumps(names, ensure_ascii=False),
            ts,
            row["id"],
        ),
    )
    conn.commit()

    slack_ok = False
    try:
        from chat_client import send_message
        msg = build_slack_text(row, set_label, rbp, best_pests, alt_count)
        result = send_message(msg)
        slack_ok = result.get("success") if isinstance(result, dict) else False
    except Exception:
        pass

    return {"ok": True, "error": None, "set_label": set_label,
            "names": names, "slack_ok": slack_ok}


def main():
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    window_end = (now + timedelta(days=get_lead_days())).strftime("%Y-%m-%d")
    lead = get_lead_days()

    log_lines = [f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 開始 (today={today}, lead={lead}d, 上限={window_end})"]

    box_vectors = load_eval_box_vectors()
    pesticide_meta = load_pesticide_meta()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute(
        """SELECT * FROM spray_schedule
           WHERE status='scheduled' AND rb_out_json IS NULL
             AND schedule_date >= ? AND schedule_date <= ?
           ORDER BY schedule_date""",
        (today, window_end),
    ).fetchall()

    if not rows:
        log_lines.append("対象行なし（生成終了）")
        finish(conn, log_lines)
        return

    processed = 0
    for row in rows:
        try:
            r = process_row(row, now, box_vectors, pesticide_meta, conn)
            if r["ok"]:
                log_lines.append(
                    f"  OK {row['schedule_date']} {r['set_label'] or '?'} → "
                    f"{', '.join(r['names']) or '(無)'} [slack={'成功' if r['slack_ok'] else '失敗/未設定'}]"
                )
                processed += 1
            else:
                log_lines.append(f"  !! {row['schedule_date']}: {r['error']}")
        except Exception as e:
            # 行単位の例外隔離: 1行で失敗しても次行に進む
            log_lines.append(f"  !! {row['schedule_date']}: 処理エラー {e}")

    log_lines.append(f"完了: 生成 {processed}/{len(rows)} 件")
    finish(conn, log_lines)


def finish(conn, log_lines):
    conn.close()
    text = "\n".join(log_lines)
    print(text)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
