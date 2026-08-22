#!/usr/bin/env python3
"""
agentic_chat/nodes.py — 状態→認知→評価→決定→投射/在庫(並列) の7ノード

各ノードは ChatState を受け取り、state の更新内容を dict で返す。
ループはない。直列DAG（有向非巡回グラフ）。

ノード一覧:
  state_node           — ① 状態: トークン集約・発火判定（Petri netモデル）
  perception_node      — ② 認知: ユーザー入力 → 病害虫ベクトル(10次元)
  evaluation_node      — ③ 評価: ベクトル → 評価BOXマッチング
  decision_node        — ④ 決定: 評価BOX + RBP行列演算 → 薬剤選定
  projection_node      — ⑤ 投射: 薬剤名・スコア・trace → メッセージテンプレート
  inventory_node       — ⑥ 在庫チェック（決定後に並列独立で発火）
  inventory_exec_node  — ⑦ 在庫実行: 在庫チェック結果の Slack送信

RBPエンジン:
  - Haskellバイナリ (rbp-algebra) を優先（レギュラー）
  - 失敗/未ビルド時は Python実装 (rbp-algebra-python/api.py) にフォールバック
  - 6段階ブリッジ(L1-L6)の通過履歴・スコア内訳を完全に再現
"""

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
    Slack にメッセージを送信する（作動レイヤ）。

    実装は SOSライブラリの実働チャンネル sos.slack に委譲する。

    Returns:
        {"success": True} または {"success": False, "error": "..."}
    """
    # sos は APP_ROOT 由来。agentic_chat/ の親ディレクトリを path に加える
    _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _app_root not in sys.path:
        sys.path.insert(0, _app_root)
    import sos

    try:
        result = sos.slack.send_message(message)
        return result if isinstance(result, dict) else {"success": False, "error": "不正な返り値"}
    except ImportError:
        logger.error("sos モジュールが見つかりません")
        return {"success": False, "error": "sos not found"}
    except Exception as e:
        logger.error(f"Slack送信エラー: {e}")
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
    ② 評価ノード — 認知した病害虫ベクトルを評価BOXにマッチさせる。

    要求評価はトップレベル evaluation.py に分離された。
    本ノードは薄アダプタ: state からベクトルを取り、evaluate に委譲する。

    Returns:
        {
            "eval_box_id": "EB-01",        # マッチした評価BOXのID
            "eval_box_name": "炭疽病",      # 人間 readable な名前
            "eval_status": "matched" | "undefined" | "none" | "error",
        }
    """
    return evaluation.evaluate(state["vector"])


# =====================================================================
# NODE ③: decision_node — 決定（仕様決定）
# =====================================================================

def decision_node(state: dict) -> dict:
    """
    ③ 決定ノード（厳密には仕様決定）— 評価BOX（または直接ベクトル）を使って、
    RBP行列演算を行い、ミラーIDでスコアリングして
    最適な薬剤セット（仕様）を選定する。

    仕様決定・RBPエンジン呼び出し（Haskell → Pythonフォールバック）は
    トップレベル decision.py に分離された。
    本ノードは薄アダプタ: state からベクトル・評価BOXを取り、decide に委譲する。

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
    return decision.decide(state["vector"], eval_box_id=state.get("eval_box_id"))


# =====================================================================
# NODE ④: projection_node — 投射
# =====================================================================

def projection_node(state: dict) -> dict:
    """
    ④ 投射ノード — 独立投射モジュール(projection.render_projection)の
    LangGraphアダプタ。

    投射ロジック本体は agentic_chat をまたいで scripts/rx_prescribe.py と
    共用するためトップレベル projection.py に分離された。
    ここはグラフのノード契約（ChatState → state更新dict）を満たすための
    薄いラッパのみ。

    Returns:
        {"projected_message": "今回の防除の薬剤は..."}
    """
    # projection は APP_ROOT 由来。agentic_chat/ の親ディレクトリを path に加える
    _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _app_root not in sys.path:
        sys.path.insert(0, _app_root)
    import projection

    return {"projected_message": projection.render_projection(state)}


# =====================================================================
# NODE ⑥: inventory_node — 在庫チェック（並列独立トランジション）
# =====================================================================

def inventory_node(state: dict) -> dict:
    """
    ⑥ 在庫チェックノード — 処方結果の薬剤名+数量で在庫を照会。

    Petri net遷移:
      処方トークン（薬剤名+数量JSON）がplaceに投入される
      → 在庫チェックが可能になったら発火

    在庫DB: stb.db（既存）のinventoryテーブル

    Returns:
        {
            "inventory_check": {"ベルクート": {"stock": 5, "needed": 3, "status": "ok", ...}, ...},
            "inventory_message": "【在庫チェック結果】...",
        }
    """
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
# NODE ⑦: inventory_exec_node — 在庫実行（並列独立トランジション）
# =====================================================================

def inventory_exec_node(state: dict) -> dict:
    """
    ⑦ 在庫実行ノード — 在庫チェック結果をSlackに送信。

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
