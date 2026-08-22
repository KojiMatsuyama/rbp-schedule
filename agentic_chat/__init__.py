"""Agentic chat — LangGraph-powered conversational backend for STB.

Framework: 状態 → 認知 → 評価 → 決定 → 投射/在庫(並列)

Petri Net parallel-transition graph:

  1. state_node        — Token aggregation (schedule/crop/environment/growth_stage)
  2. perception_node   — User input → 10-dim disease/pest vector
  3. evaluation_node   — Vector → EvalBox matching (requirement evaluation)
  4. decision_node     — EvalBox + RBP matrix calc → pesticide selection
  5. projection_node   — Drug names → message template
  6. inventory_node    — Stock check for prescribed drugs (parallel)
  7. inventory_exec    — Send to Slack (inventory result)

After decision_node, the prescription token is released into state,
triggering TWO independent transitions (projection and inventory)
that converge at END.
"""

import logging
import os
from typing import Optional

from .graph import build_graph
from .nodes import _strip_reasoning, classify_intent

logger = logging.getLogger(__name__)

# Compile once at import time (singleton)
_app = build_graph()


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
        thread_id: LangGraph thread for conversation history.

    Returns:
        The projected message text (final answer).
    """
    from .state import ChatState

    tid = thread_id or conversation_id or "default"

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
        "eval_box_id": None,
        "eval_box_name": None,
        "eval_status": None,
        "prescription": [],
        "mirror_id": None,
        "effectiveness": None,
        "line_traces": [],
        "excluded_drugs": [],
        "excluded_combos": [],
        "projected_message": None,
        "inventory_check": None,
        "inventory_message": None,
        "executed_projection": False,
        "executed_inventory": False,
        "sent_to": None,
        "error": None,
    }

    config = {"configurable": {"thread_id": tid}}

    # Execute the full Petri Net pipeline (parallel transitions)
    result = _app.invoke(state, config=config)

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
