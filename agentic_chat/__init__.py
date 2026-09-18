"""Agentic chat — 状態遷移ネット駆動の対話バックエンド for STB.

Framework: 状態 → 認知 → 要求評価 → 薬剤選定 → 投射1 / 投射2(在庫用)→在庫

スケジューラは 状態遷移ネット本体（jobnet.run_net が NET["edges"] を走査）。
LangGraph は廃止された。

Petri Net parallel-transition graph (token-driven transitions):

  1. state_node         — Token aggregation (field_type/pest_matrix)
  2. perception_node    — User input → 10-dim disease/pest vector
  3. evaluation_node    — 要求評価: Vector → EvalBox matching, plus 投射2(選定用)
                          issuing selection_token into the selection place
  4. decision_node      — 薬剤選定: reads selection place, RBP matrix calc → prescription
  5. projection_node    — 投射1: Drug names → diagnostic report message
  6. rx_exec_node       — 作動: Slack送信(処方). Records send completion in HUMOS
                          (write_sos) and, on success, places inventory_token into
                          the inventory place (place_token) — 石3 在庫リフレックス
  7. inventory_node     — 在庫チェック: reads inventory place, stock check
  8. inventory_exec_node— Send to Slack (inventory result)

After decision_node, the prescription is released, firing TWO branches:
投射1 (report) and 作動:Slack送信処方 (rx_exec_node). rx_exec_node sends the
prescription to Slack; on completion HUMOS records it (write_sos), judges the
state, and places inventory_token into the inventory place (place_token) so the
在庫チェック transition fires once that place holds a token (間接的開き・§4.3).
"""

import logging
import os
from typing import Optional

import jobnet
from .nodes import _strip_reasoning, classify_intent

logger = logging.getLogger(__name__)


def run(
    message: str,
    *,
    conversation_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> str:
    """Run the Petri Net parallel-transition pipeline.

    Args:
        message: User input text.
        conversation_id: Legacy conv ID (kept for API compat).
        thread_id: Legacy (was LangGraph thread; kept for API compat, unused).

    Returns:
        The projected message text (final answer).
    """
    from .state import ChatState

    # ================================================================
    # 第一段階の意図分類（認知ノードより前）
    # ================================================================
    # 雑談・無関係入力（「こんにちは」「ありがとう」「天気はどう？」）は
    # RBPパイプラインに渡さず、LLMにそのまま答える。
    # ここで "chat" なら _llm_chat に即ルーティングして return する。
    # （perception の LLM 病害虫推論が雑談を hallucinate して
    #   処方結果を返すバグの防止。意図が確定してからRBPを回す。）
    if classify_intent(message) == "chat":
        return _llm_chat(message) or _fallback_chat_reply(message)

    state: ChatState = {
        "messages": [{"role": "user", "content": message}],
        "intent": None,
        "identified_diseases": [],
        "vector": [0] * 10,
        "eval_token": None,
        "eval_box_id": None,
        "eval_box_name": None,
        "eval_status": None,
        "selection_token": None,
        "prescription": [],
        "mirror_id": None,
        "effectiveness": None,
        "line_traces": [],
        "excluded_drugs": [],
        "excluded_combos": [],
        "projected_message": None,
        "inventory_token": None,
        "inventory_check": None,
        "inventory_message": None,
        "executed_projection": False,
        "executed_inventory": False,
        "sent_to": None,
        "error": None,
    }

    # Execute the full Petri Net pipeline — スケジューラは 状態遷移ネット本体
    # （jobnet.run_net が NET["edges"] を走査し各トランジションを発火）
    # LangGraph は廃止された。
    result = jobnet.run_net(state)

    # 意図ルーティング:
    #   intent="chat"（病害虫が認知されない = 雑談・無関係入力）
    #     → RBP処方結果を返さず、LLMにそのまま答えさせる。
    #   intent="disease"（病害虫が認知された）
    #     → 通常の処方メッセージ（projected_message）を返す。
    intent = result.get("intent") or (
        "disease" if sum(result.get("vector") or []) > 0 else "chat"
    )
    if intent == "chat":
        return _llm_chat(message) or _fallback_chat_reply(message)

    # Return the projected message
    return result.get("projected_message") or "エラー: 応答がありません"


# =====================================================================
# 雑談LLM経路（intent="chat" の場合）
# =====================================================================

_LOCAL_LLM_BASE_URL = os.environ.get(
    "ANTHROPIC_BASE_URL", "http://192.168.131.161:24200"
)
_LOCAL_LLM_MODEL = os.environ.get("ANTHROPIC_MODEL", "local-llm")
_LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-litellm-test-1234")

_CHAT_SYSTEM_PROMPT = (
    "あなたは農薬防除アプリのチャットアシスタントです。"
    "植物の病害虫への相談には、症状（例:「実が腐ってる」「葉が黄色い」）を"
    "詳しく伝えるよう促し、その上でRBP処方を行うことを案内してください。"
    "雑談・質問には、簡潔かつ親しみやすく日本語で答えてください。"
    "症状の相談でない限り、薬剤名を挙げてはなりません。"
)


def _llm_chat(message: str) -> str:
    """
    雑談・無関係入力（「こんにちは」「天気はどう？」等）をローカルLLM
    （Qwen3.6-35B via LiteLLM の OpenAI互換エンドポイント）にそのまま
    答える。nodes.py の _llm_guess_vector と同じ呼び出し経路。

    Returns:
        LLMの応答文字列。失敗（接続不能・空応答）時は空文字列を返す。
    """
    try:
        import requests

        url = f"{_LOCAL_LLM_BASE_URL}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_LITELLM_API_KEY}",
        }
        payload = {
            "model": _LOCAL_LLM_MODEL,
            "messages": [
                {"role": "system", "content": _CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            "max_tokens": 512,
            "temperature": 0.7,
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        msg = data["choices"][0]["message"]
        text = msg.get("content") or msg.get("reasoning_content", "")
        return _strip_reasoning(text) or ""
    except Exception as e:
        logger.warning(f"Chat LLM failed: {e}")
        return ""


def _fallback_chat_reply(message: str) -> str:
    """
    LLMが使えない時の雑談フォールバック（キーワードベースの定型応答）。
    薬剤名を挙げない（「アブラムシに効く？」等の曖昧な相談は
    RBPエンジン側が辞書で認知するため）。
    """
    msg = message.strip().lower()
    if any(g in message for g in ("こんにちは", "やあ", "はじめまして", "おはよう", "こんばんは")):
        return (
            "こんにちは！農薬防除アプリのAIアシスタントです。\n"
            "病害虫の症状（例:「実が腐ってる」「葉が黄色い」）を教えていただくと、"
            "最適な薬剤をRBP処方でお答えします。"
        )
    if "ありがとう" in message:
        return "お役に立てて嬉しいです！他に気になる症状があれば、お気軽にどうぞ。"
    if any(k in msg for k in ("天気", "気象", "予報", "temperature")):
        return (
            "天気情報はここで確認できません。"
            "病害虫の発生には気候が大きく影響するので、"
            "気象情報を参考にしながら症状の報告をしてください。"
        )
    # 病害虫が認知されない入力 → 汎用案内（処方結果は返さない）
    return (
        "申し訳ありません。植物の病害虫の相談以外のお手伝いは苦手です。\n"
        "症状（例:「実が腐ってる」「葉に白い粉が吹いてる」）を教えていただくと、"
        "薬剤のRBP処方を行います。"
    )
