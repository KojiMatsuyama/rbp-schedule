#!/usr/bin/env python3
"""防除暦（spray_schedule）の処方自動生成 — cron 本体。

毎日朝に cron から実行される。対象は:
    status='scheduled' かつ rb_out_json IS NULL かつ
    today <= schedule_date <= today + RX_LEAD_DAYS
の行。各行について、一本のシーケンスを厳密に実行する:

  ① 防除暦ボタン押下（イベント発生）        — UI「⚡今すぐ」/ cron
  ② 病害虫予測ベクトル生成                  — set_ids「セットN」→ BOX-NN.vector
  ③ 認知（トークンバリデーション）          — 日付・set_ids/BOX・ベクトルの妥当性
       → 妥当でなければログを書いて exit（その行スキップ）、OKなら続行
  ④ 要求評価RBP → ⑤ 評価BOX導出 → ⑥ ミラーID → ⑦ 仕様決定RBP → ⑧ 薬剤導出
       ※ ④〜⑧ は rbp-algebra-python の api.prescribe(vector) 1呼び出しが
          原子的に算出する（要求評価＝BOX完全一致、仕様決定＝ミラーIDでセット選定）。
          戻りJSON から段階を対応づけてログ出力する。
  ⑨ 投射（Slack薬剤がパラメータの文章作成） — build_slack_text（ミラーIDを含む）
  ⑩ 作動（Slack送信）                       — chat_client.send_message

副次的に spray_schedule を更新する:
  - pesticide_ids = 薬剤名配列（UI が素通し表示するため）
  - rb_out_json   = 構造化結果（id+希釈率+ミラーID+スコア+代替数）

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

sys.path.insert(0, APP_ROOT)  # chat_client / RBP api / projection の import 用

# ⑨ 投射（Slack文章生成）は独立投射モジュールに分離（cron とチャットの両方で共用）
# ⑩ 作動（Slack送信）は SOSライブラリの実働チャンネル sos.slack として管理
import projection  # noqa: E402
import perception  # noqa: E402
import sos  # noqa: E402


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


def process_row(row, now, box_vectors, pesticide_meta, conn):
    """1行分を一本のシーケンス ②〜⑩ で処理する。

    ② 病害虫予測ベクトル生成 → ③ 認知(バリデーション) →
       ④ 要求評価RBP → ⑤ 評価BOX導出 → ⑥ ミラーID → ⑦ 仕様決定RBP → ⑧ 薬剤導出
       (④〜⑧ は api.prescribe 1呼び出しで原子的に算出) →
       ⑨ 投射(Slack文章) → ⑩ 作動(Slack送信)

    戻り値 dict:
      ok: bool            — 生成完了したか（③認知失敗時 False）
      error: str          — 失敗時の理由（ok=False のとき）
      set_label: str      — 「セットN」
      names: [str]        — 処方された薬剤名
      slack_ok: bool      — Slack送信成功したか
      stages: [str]       — 各段階の要約行（ログ出力用）
      mirrorId: float|None — ⑥ ミラーID
    """
    stages = []
    set_label = ""
    names = []

    def done(err=None, extra=None):
        out = {"ok": err is None, "error": err, "set_label": set_label,
               "names": names, "slack_ok": False, "stages": stages,
               "mirrorId": None}
        out.update(extra or {})
        return out

    # ② 病害虫予測ベクトル生成 — set_ids「セットN」→ BOX-NN.vector
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
    vector = box["vector"] if box else None
    if box:
        stages.append(f"②病害虫予測ベクトル生成: {set_label} → {box_key} → {vector}")
    else:
        stages.append(f"②病害虫予測ベクトル生成: {set_label or 'セット未設定'} → {box_key or 'BOX不明'}")

    # ③ 認知（第一トランジション: トークンチェック）— まずトークンがそろっているか
    #    日付・set_ids/BOX のそろいチェック → ベクトルの次元・型チェック
    #    （変数の型チェックと同様: 行列は「何行何列か」、要素は2値。深い検査はしない）
    #    妥当でなければログを書いて exit（その行スキップ）
    if not row["schedule_date"]:
        stages.append("③認知: NG (日付欠落)")
        return done("日付欠落")
    if not set_num:
        stages.append("③認知: NG (set_ids不正)")
        return done("セット未設定")
    if box is None:
        stages.append(f"③認知: NG (BOX未対応: {box_key})")
        return done(f"BOX 未対応（{set_label}）")
    ok, vec_err = perception.check_vector(vector)
    if not ok:
        stages.append(f"③認知: NG (ベクトル不正: {vector} [{vec_err}])")
        return done(f"ベクトル不正（{box_key}）")
    stages.append("③認知: OK")

    # ④〜⑧: api.prescribe(vector) 1呼び出し。戻りJSONから段階を対応づける
    rbp = run_rbp(vector)
    if isinstance(rbp, dict) and rbp.get("error"):
        stages.append(f"④要求評価RBP: NG ({rbp['error']})")
        return done(rbp["error"])

    eb = rbp.get("evalBox") or {}
    eb_status = eb.get("status")
    eb_detail = eb.get("detail")
    best = rbp.get("best") or {}
    mirror = best.get("mirrorId")

    # ④ 要求評価RBP / ⑤ 評価BOX導出
    #    要求評価はBOX完全一致。MATCHなら eb_detail が導出した BOX id。
    #    UNDEFINED（新規組合せ）は導出不能、ERROR は複数一致。
    derived = eb_detail if eb_detail else "導出不能（" + str(eb_status) + "）"
    stages.append(f"④要求評価RBP: {eb_status}")
    stages.append(f"⑤評価BOX導出: {derived}")
    # ⑥ ミラーID
    stages.append(f"⑥ミラーID: {mirror:.4f}" if isinstance(mirror, (int, float)) else "⑥ミラーID: -")
    # ⑦ 仕様決定RBP / ⑧ 薬剤導出
    best_pests = enrich_pesticides(best.get("pesticides"), pesticide_meta) if best else []
    names = [p["name"] for p in best_pests if p.get("name")]
    if not best:
        stages.append(f"⑦仕様決定RBP: 該当なし (status={rbp.get('status')})")
    else:
        stages.append(f"⑦仕様決定RBP: {len(names)}剤セット (スコア {best.get('totalScore')})")
    stages.append(f"⑧薬剤導出: {', '.join(names) if names else '該当薬剤なし'}")

    alt_count = len(rbp.get("alternatives") or [])
    rb_out = {
        "set": set_num,
        "setLabel": set_label,
        "box": box_key,
        "vector": vector,
        "rbp_status": rbp.get("status"),
        "evalBox": {"status": eb_status, "detail": eb_detail},
        "best": {
            "pesticides": best_pests,
            "mirrorId": mirror,
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

    # ⑨ 投射（Slack薬剤がパラメータの文章作成）— 独立投射モジュール
    msg = projection.build_slack_text(row, set_label, rbp, best_pests, alt_count)
    stages.append("⑨投射: Slack文章生成")

    # ⑩ 作動（Slack送信）— SOSライブラリの実働チャンネル
    result = sos.slack.send_message(msg)
    slack_ok = result.get("success") if isinstance(result, dict) else False
    stages.append(f"⑩作動: Slack送信 {'成功' if slack_ok else '未設定/失敗'}")

    return done(None, extra={"names": names, "slack_ok": slack_ok, "mirrorId": mirror})


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
                for st in r.get("stages", []):
                    log_lines.append(f"      · {st}")
                processed += 1
            else:
                log_lines.append(f"  !! {row['schedule_date']}: {r['error']}")
                for st in r.get("stages", []):
                    log_lines.append(f"      · {st}")
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
