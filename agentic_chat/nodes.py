#!/usr/bin/env python3
"""
agentic_chat/nodes.py — 状態→認知→評価→決定→投射/在庫(並列) の7ノード

各ノードは ChatState を受け取り、state の更新内容を dict で返す。
ループはない。直列DAG（有向非巡回グラフ）。

ノード一覧:
  state_node           — ① 状態: トークン集約・発火判定（Petri netモデル）
  perception_node      — ② 認知: ユーザー入力 → 病害虫ベクトル(10次元)
  evaluation_node      — ③ 要求評価: ベクトル → 評価BOXマッチング
                          ＋ 投射2(選定用): 薬剤選定プレースへ selection_token 発行
  decision_node        — ④ 薬剤選定: 選定プレースから読み、RBP行列演算 → 処方
  projection_node      — ⑤ 投射1: 薬剤名・スコア・trace → メッセージテンプレート
  rx_exec_node         — ⑥ 作動: Slack送信(処方)。送信完了を HUMOS の状態にし、
                          状態判断で在庫プレースに処方トークンを置く（石3・在庫リフレックス）
  inventory_node       — ⑦ 在庫チェック: 在庫プレースから読み、在庫DB照会
  inventory_exec_node  — ⑧ 在庫実行: 在庫チェック結果の Slack送信

RBPエンジン:
  - Haskellバイナリ (rbp-algebra) を優先（レギュラー）
  - 失敗/未ビルド時は Python実装 (rbp-algebra-python/api.py) にフォールバック
  - 6段階ブリッジ(L1-L6)の通過履歴・スコア内訳を完全に再現
"""

import json
import logging
import math
import os
import sys
from datetime import datetime, date

logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =====================================================================
# 認知モジュールの再エクスポート
# =====================================================================
# 認知クラスタ（症状辞典・classify_intent・_strip_reasoning・ベクトル変換・
# LLM推論）はトップレベル perception.py に分離された。
# agentic_chat/__init__.py が従来この2つを nodes 経由で import しているため、
# import 解決を保つ（本物の定義は perception.py）。
import perception  # noqa: E402
import evaluation  # noqa: E402
import decision  # noqa: E402
import state  # noqa: E402

# 投射（トップレベル）— 投射2（作動つき投射）のレンダラを使うため。
# agentic_chat/ の親ディレクトリ（APP_ROOT）を path に加えて解決する。
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
import projection  # noqa: E402

_strip_reasoning = perception._strip_reasoning  # noqa: F841  (legacy re-export)
classify_intent = perception.classify_intent  # noqa: F841  (legacy re-export)


# =====================================================================
# 注: トランジション本体はトップレベルモジュールに分離された
#   - 認知: perception.py（perception_node が薄アダプタ）
#   - 評価(要求評価): evaluation.py（evaluation_node が薄アダプタ）
#   - 決定(仕様決定 + Haskell RBPエンジン呼び出し): decision.py
#     （decision_node が薄アダプタ）
# =====================================================================


# =====================================================================
# ヘルパー: Slack送信
# =====================================================================

def _send_to_slack(message: str) -> dict:
    """
    メッセージを実働に届ける（作動レイヤ）。

    実装は IF（膜）ファサード sos.membrane.deliver に委譲する（IF 設計 §8）。
    現行はチャネルが Slack の1つなので SlackAdapter 経由（挙動不変）。
    write_sos（作動ログ）は sos.slack 内で呼ばれる（本経路では二重記録しない）。

    Returns:
        {"success": True} または {"success": False, "error": "..."}
    """
    # sos は APP_ROOT 由来。agentic_chat/ の親ディレクトリを path に加える
    _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _app_root not in sys.path:
        sys.path.insert(0, _app_root)
    import sos

    try:
        result = sos.membrane.deliver(message)
        return result if isinstance(result, dict) else {"success": False, "error": "不正な返り値"}
    except ImportError:
        logger.error("sos モジュールが見つかりません")
        return {"success": False, "error": "sos not found"}
    except Exception as e:
        logger.error(f"作動送信エラー: {e}")
        return {"success": False, "error": str(e)[:200]}


# =====================================================================
# NODE ①: state_node — 状態（トークン集約・発火判定）[再エクスポート]
# =====================================================================
# 状態本体（トークンストア＝Petri網のプレース ＋ 発火判定）はトップレベル state.py に
# 分離された。graph.py が .nodes 経由で state_node を import しているため、
# import 解決を保つ（本物の定義は state.py）。
state_node = state.state_node  # noqa: F841  (legacy re-export)


# =====================================================================
# NODE ②: perception_node — 認知
# =====================================================================

def perception_node(state: dict) -> dict:
    """
    ① 認知ノード — 独立認知モジュール(perception.perceive)のLangGraphアダプタ。

    認知ロジック本体は agentic_chat をまたいで scripts/rx_prescribe.py（③認知）
    と共用するためトップレベル perception.py に分離された。ここはグラフのノード
    契約（ChatState → state更新dict）を満たすための薄いラッパのみ。

    Returns:
        {
            "identified_diseases": ["炭疽病", "アブラムシ"],
            "vector": [1, 0, 0, 0, 0, 0, 0, 0, 1, 0],
        }
    """
    user_input = perception._get_last_user_message(state["messages"])
    return perception.perceive(user_input)


# =====================================================================
# NODE ②: evaluation_node — 評価（要求評価）
# =====================================================================

def evaluation_node(state: dict) -> dict:
    """
    ③ 要求評価ノード ＋ 投射2（選定用）— ベクトル→評価BOX分類と、薬剤選定プレース
    への selection_token 発行を行う。

    1. 要求評価（トップレベル evaluation.py）: 認知した病害虫ベクトルを
       評価BOXにマッチさせる。ベクトルは「評価トランジションの入力プレース」
       eval_token から復元する（認知の出力 vector をフォールバックに持つ）。
    2. 投射2（選定用）: 作動として、薬剤選定（決定）トランジションの入力
       プレースへ発行する selection_token（ベクトル＋評価BOXのJSON）を生成し、
       決定トランジションを発火させる。

    Returns:
        {
            "eval_box_id": "EB-01",        # マッチした評価BOXのID
            "eval_box_name": "炭疽病",      # 人間 readable な名前
            "eval_status": "matched" | "undefined" | "none" | "error",
            "selection_token": '{"vector":[...], "eval_box_id":"EB-01"}',
        }
    """
    # 評価トランジションの入力プレース（eval_token）からベクトルを復元する。
    # eval_token は本ノードが前回投入したものであり、認知の vector と等価。
    # 空の場合は認知の vector をフォールバック（直結発火との互換維持）。
    vec = state["vector"]
    if state.get("eval_token"):
        try:
            vec = json.loads(state["eval_token"])
        except (ValueError, TypeError):
            vec = state["vector"]

    # 融合: conn（state["_conn"]、共有 OS が注入）があれば DB駆動 rbp_engine。
    result = evaluation.evaluate(vec, conn=state.get("_conn"))
    eval_box_id = result.get("eval_box_id")

    # 投射2（選定用）— 薬剤選定プレースへ selection_token を発行する（作動）。
    out = dict(result)
    out["selection_token"] = projection.render_selection_token(vec, eval_box_id)
    return out


# =====================================================================
# NODE ③: decision_node — 決定（仕様決定）
# =====================================================================

def decision_node(state: dict) -> dict:
    """
    ④ 薬剤選定（決定）ノード — 選定プレース（selection_token）からベクトル・
    評価BOXを復元し、RBP行列演算でミラーIDスコアリングし、最適な薬剤セット
    （仕様）を選定する。

    仕様決定・RBPエンジン呼び出し（Haskell → Pythonフォールバック）は
    トップレベル decision.py に分離された。
    本ノードは薄アダプタ: 入力プレース（selection_token）からベクトル・評価BOXを
    復元し、decide に委譲する。

    Returns:
        {
            "prescription": [...],
            "alternatives": [...],
            "mirror_id": 0.95,
            "effectiveness": 45.2,
            "bridge_trace": "L1: PASS ...\n...",
            "excluded_drugs": ["ベルクート: PHI不足"],
            "excluded_combos": ["ベルクート+ダコニール: 混用不可"],
            "status": "SUCCESS",
        }
    """
    # 薬剤選定（決定）トランジションの入力プレース（selection_token）から
    # ベクトル・評価BOXを復元して発火する。投射2（選定用）が投入したもので、
    # 認知の vector / 要求評価の eval_box_id と等価。空の場合はそれらを
    # フォールバック（直結発火との互換維持）。
    vector = state["vector"]
    eval_box_id = state.get("eval_box_id")
    if state.get("selection_token"):
        try:
            tok = json.loads(state["selection_token"])
            vector = tok.get("vector", state["vector"])
            if tok.get("eval_box_id") is not None:
                eval_box_id = tok["eval_box_id"]
        except (ValueError, TypeError):
            pass
    # 融合: conn（state["_conn"]、共有 OS が注入）があれば DB駆動 rbp_engine。
    return decision.decide(vector, eval_box_id=eval_box_id, conn=state.get("_conn"))


# =====================================================================
# NODE ④: projection_node — 投射
# =====================================================================

def projection_node(state: dict) -> dict:
    """
    ⑤ 投射1ノード — 処方結果を人間が読む診断レポートに写像する。

    独立投射モジュール(projection.render_projection)のLangGraphアダプタ。
    投射ロジック本体は agentic_chat をまたいで scripts/rx_prescribe.py と
    共用するためトップレベル projection.py に分離された。
    ここはグラフのノード契約（ChatState → state更新dict）を満たすための
    薄いラッパのみ。

    注意: 投射1はレポート作成に専念し、作動（トークン発行）は持たない。
    処方トークンの在庫プレースへの発行は作動（rx_exec_node・Slack送信処方）が
    送信完了後に HUMOS 経由で行う（石3・在庫リフレックス）。

    Returns:
        {"projected_message": "今回の防除の薬剤は..."}
    """
    return {"projected_message": projection.render_projection(state)}


# =====================================================================
# NODE ⑥: rx_exec_node — 作動：Slack送信（処方）＋在庫リフレックス（石3）
# =====================================================================

def rx_exec_node(state: dict) -> dict:
    """
    ⑥ 作動：Slack送信（処方）— 処方メッセージを Slack に送信し、送信完了を
    HUMOS の状態にする（石3・在庫リフレックス）。

    「作動完了が状態である」（ts/TS設計.md §4.3）の 3 段:
      1. 記録   — sos.slack.send_message が humos.write_sos("slack_send", result)
      2. 状態判断 — 送信成功か・処方が空でないか
      3. 次トークンを置く — humos.place_token("inventory_token", 処方JSON, "T_rx_exec")

    ネットは inventory_token を handler の返り値では**持たない**。HUMOS がマーキング
    に置いた在庫トークンは、run_net のループ先頭でライブマーキングを再読して発見され、
    T_inventory が発火する（間接的開き・能動ゲートの具体）。

    送信失敗・処方空のときは在庫トークンを置かない → T_inventory 未発火 →
    固定点で停止（§8.4「待ちが詰まる→審議へエスカレーション」の足場）。

    Returns:
        {"external_wait_token": '{"sent": true}'}
        （「ネットの状態＝Slack送信完了を待っている」の彩色トークン・§8.2。
         在庫トークンは返さず place_token で HUMOS に置く）
    """
    # 関数内 import（循環回避: nodes → humos はモジュールレベルで安全だが、
    # 遅延解決の既存パターンに揃える）
    import humos

    prescription = state.get("prescription", [])
    # 作動: Slack送信（処方メッセージ）。write_sos は send_message 内で呼ばれる。
    message = projection.render_projection(state)
    result = _send_to_slack(message)
    sent = bool(result.get("success"))

    out = {"external_wait_token": json.dumps({"sent": sent}, ensure_ascii=False)}
    if sent and prescription:
        # 状態判断: Slack送信完了 → 在庫チェックに進む（次トークンを置く）
        humos.place_token(
            "inventory_token",
            projection.render_inventory_token(prescription),
            trigger="T_rx_exec",
        )
    return out


# =====================================================================
# NODE ⑦: inventory_node — 在庫チェック（在庫プレースから発火）
# =====================================================================

def inventory_node(state: dict) -> dict:
    """
    ⑦ 在庫チェックノード — 処方結果の薬剤名+数量で在庫を照会。

    Petri net遷移:
      在庫チェックの入力プレース（inventory_token）に処方トークンが投入される
      → 在庫チェックが発火

    在庫DB: stb.db（既存）のinventoryテーブル

    Returns:
        {
            "inventory_check": {"ベルクート": {"stock": 5, "needed": 3, "status": "ok", ...}, ...},
            "inventory_message": "【在庫チェック結果】...",
        }
    """
    # 在庫チェックの入力プレース（inventory_token）から処方トークンを復元する。
    # 投射2（在庫用）が投入した処方JSON（薬剤名+数量）。空の場合は
    # state["prescription"] をフォールバック（直結発火との互換維持）。
    prescription = state.get("prescription", [])
    if state.get("inventory_token"):
        try:
            prescription = json.loads(state["inventory_token"])
        except (ValueError, TypeError):
            prescription = state.get("prescription", [])

    if not prescription:
        return {
            "inventory_check": {},
            "inventory_message": "【在庫チェック結果】\n処方結果がありません。",
        }

    # 各薬剤の在庫をチェック
    inventory_check: dict[str, dict] = {}
    for drug in prescription:
        name = drug.get("name", "?")
        needed = drug.get("quantity", 3)

        # 在庫DBから照会
        stock = query_stock_from_db(name)

        if stock is None:
            status = "unknown"
            message = f"{name}: 在庫情報なし"
        elif stock >= needed:
            status = "ok"
            message = f"{name}: 在庫あり（在庫:{stock}, 必要:{needed}）"
        else:
            status = "insufficient"
            message = f"{name}: 在庫不足（在庫:{stock}, 必要:{needed}, 不足:{needed - stock}）"

        inventory_check[name] = {
            "stock": stock,
            "needed": needed,
            "status": status,
            "message": message,
        }

    # メッセージを構築
    lines = ["【在庫チェック結果】"]
    for drug in prescription:
        info = inventory_check[drug.get("name", "?")]
        lines.append(info["message"])

    # 不足分があれば強調
    insufficient = [
        d for d in prescription
        if inventory_check.get(d.get("name", ""), {}).get("status") == "insufficient"
    ]
    if insufficient:
        lines.append("")
        lines.append("⚠ 在庫不足の薬剤:")
        for d in insufficient:
            info = inventory_check.get(d.get("name", ""), {})
            stock_val = info.get("stock", "?")
            needed_val = info.get("needed", "?")
            lines.append(f"  - {d.get('name', '?')}: 不足{needed_val - stock_val}個")

    return {
        "inventory_check": inventory_check,
        "inventory_message": "\n".join(lines),
    }


# =====================================================================
# NODE ⑧: inventory_exec_node — 在庫実行（並列独立トランジション）
# =====================================================================

def inventory_exec_node(state: dict) -> dict:
    """
    ⑧ 在庫実行ノード — 在庫チェック結果をSlackに送信。

    投射トランジションとは独立して動作。

    Returns:
        {"executed_inventory": True, "sent_to": "slack"}
    """
    message = state.get("inventory_message", "")

    if not message:
        return {
            "executed_inventory": False,
            "sent_to": None,
            "error": "送信メッセージが空です",
        }

    result = _send_to_slack(message)

    if result.get("success"):
        return {
            "executed_inventory": True,
            "sent_to": "slack",
        }
    else:
        return {
            "executed_inventory": False,
            "sent_to": None,
            "error": result.get("error", "Slack送信に失敗しました"),
        }


# =====================================================================
# ヘルパー: 在庫DB照会
# =====================================================================

def query_stock_from_db(pesticide_name: str) -> int | None:
    """
    薬剤名から在庫数を取得。

    在庫DB: stb.db の inventory テーブル
    テーブル構造:
        CREATE TABLE inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pesticide_id TEXT UNIQUE,
            pesticide_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit TEXT DEFAULT '本',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

    Args:
        pesticide_name: 薬剤名（例: "ベルクート"）

    Returns:
        在庫数（int）または存在しない場合は None
    """
    import os
    import sqlite3

    # stb.db のパスを特定（data/ディレクトリ）
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "stb.db")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT quantity FROM inventory WHERE pesticide_name = ?",
            (pesticide_name,),
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        # DBが存在しない、テーブルがない等の場合は None
        return None
