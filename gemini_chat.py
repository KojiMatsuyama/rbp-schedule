#!/usr/bin/env python3
"""
gemini_chat.py — Gemini 2.5 Pro を使ったチャットバックエンド

mcp_tools.py の関数を Gemini のツールとして呼び出す。
server.py から import して使う。
google-genai 2.x 対応。
"""

import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import — only when needed
_genai = None


def _get_genai():
    global _genai
    if _genai is None:
        try:
            import google.genai as g
            _genai = g
        except ImportError:
            logger.error("google-genai がインストールされていません。pip install google-genai を実行してください。")
            return None
    return _genai


def _get_api_key() -> Optional[str]:
    """Load GEMINI_API_KEY from environment or .env file."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    # Fallback: read .env file
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "GEMINI_API_KEY" and v.strip():
                    return v.strip()
    return None


def is_configured() -> bool:
    """Check if Gemini API key is configured."""
    return _get_api_key() is not None and _get_api_key() != ""


SYSTEM_PROMPT = """あなたは農業の専門AIアシスタントです。STB防除スケジューラのデータにアクセスできます。

## ルール
- 日本語で回答する
- ツールを使ってデータを取得し、具体的な情報を伝える
- わからないことは「データがありません」と正直に言う
- 薬剤の推奨には常にPHI（収穫待期日）、散布回数、毒性区分を明記する
- 過剰な保証や責任ある医療助言のような表現は避ける
- データに基づく客観的な情報を提供することを心がける
- ツールで取得したデータはそのまま引用し、脚色しない
- 複数回のツール呼び出しが必要な場合は、順番に実行して統合した回答をする

## 🔎 RAG検索 — 質問が曖昧な時は必ず使う

ユーザーの質問が以下のいずれかに当てはまる場合、**最初に `retrieve_context` ツールを呼び出してください**:

1. **症状描述**: 「葉っぱが黄色い」「丸まってる」「白い粉が吹いてる」など
2. **有機・規格**: 「有機JAS対応」「登録農薬」など
3. **経験ベース**: 「去年みたいに」「前と同じように」など
4. **混用相談**: 「◯◯と混ぜられるか」など

retrieve_context で取得したコンテキストを元に、その後適切なツールを呼び出して回答してください。
"""


def _local_search(message: str) -> str:
    """Geminiなしのローカル検索モード。DBからデータを引っ張って整形して返す。"""
    from mcp_tools import (
        search_pesticides,
        list_pesticides,
        list_diseases,
        get_spray_history,
        summarize_history,
        get_current_season_advice,
    )

    msg = message.lower()

    # --- 季節のアドバイス ---
    if any(w in msg for w in ["季節", "季節のアドバイス", "今日の季節"]):
        return get_current_season_advice()

    # --- 今月の履歴 ---
    if any(w in msg for w in ["今月", "今月の履歴", "今月の記録"]):
        return summarize_history("month")

    # --- 今週の履歴 ---
    if any(w in msg for w in ["今週", "今週の履歴"]):
        return summarize_history("week")

    # --- 殺菌剤一覧 ---
    if "殺菌剤" in msg or "fungicide" in msg:
        return list_pesticides(category="fungicide")

    # --- 殺虫剤一覧 ---
    if "殺虫剤" in msg or "insecticide" in msg:
        return list_pesticides(category="insecticide")

    # --- 殺ダニ剤一覧 ---
    if "殺ダニ剤" in msg or "acaricide" in msg:
        return list_pesticides(category="acaricide")

    # --- 薬剤一覧 ---
    if "薬剤" in msg or "一覧" in msg or "全部" in msg or "すべて" in msg:
        return list_pesticides(limit=100)

    # --- 病害虫マスター ---
    if "病害虫" in msg or "マスター" in msg or "リスト" in msg:
        return list_diseases()

    # --- 病害一覧のみ ---
    if "病害" in msg and "害虫" not in msg:
        return list_diseases(disease_type="disease")

    # --- 害虫一覧のみ ---
    if "害虫" in msg and "病害" not in msg:
        return list_diseases(disease_type="pest")

    # --- 防除履歴 ---
    if "履歴" in msg or "記録" in msg or "レコード" in msg:
        return get_spray_history(limit=50)

    # --- 病害虫名での検索（炭疽病、うどんこ病、アブラムシなど） ---
    keywords = ["炭疽", "うどんこ", "灰色かび", "アブラムシ", "コナジラミ",
                 "ハダニ", "ネキリムシ", "モザイク", "べと病", "さび病",
                 "紋羽", "徒長", "軟腐", "疫病", "黒星", "斑点",
                 "アオハダニ", "ヨトウガ", "シンクワムシ", "トンボ",
                 "ホソアカムシ", "カイガラムシ", "ナメクジ", "ネマトーダ"]
    found_kw = [kw for kw in keywords if kw in msg]
    if found_kw:
        kw = found_kw[0]
        result = search_pesticides(kw)
        if result:
            return result

    # --- 処方（ベクトル） ---
    if "処方" in msg or "処方箋" in msg or "RBP" in msg:
        return "⚠️ 処方機能には10次元の要求ベクトルが必要です。\n\nメイン画面から病害虫を選択すると自動的に計算されます。"

    # --- デフォルト: 何らかのヒントを返す ---
    return (
        f"🔍 「{message}」について検索しました。\n\n"
        f"以下のコマンドを試してみてください：\n"
        f"• 薬剤の一覧を出して\n"
        f"• 殺菌剤の一覧を出して\n"
        f"• アブラムシに効く薬剤を教えて\n"
        f"• 病害虫マスターを見せて\n"
        f"• 今月の防除履歴を教えて\n"
        f"• 今日の季節のアドバイス\n\n"
        f"※ Gemini APIキーを設定すると、より高度なAI回答が得られます。\n"
        f"取得方法: https://aistudio.google.com/apikey"
    )


def _make_tools(selected_tools=None, genai=None):
    """
    Build Gemini tool definitions from TOOL_REGISTRY.

    Args:
        selected_tools: ツール名のリスト（None で全ツール）
        genai: google.genai module（必須）
    """
    from mcp_tools import TOOL_REGISTRY

    if selected_tools is None:
        registry = TOOL_REGISTRY
    else:
        registry = [t for t in TOOL_REGISTRY if t["name"] in selected_tools]

    tools = []
    for t in registry:
        schema = t["input_schema"]
        tools.append(genai.types.Tool(
            function_declarations=[genai.types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=schema,
            )]
        ))
    return tools


def _build_contents(message: str, history: list, genai):
    """Build conversation contents list."""
    contents = []
    if history:
        for role, text in history[-10:]:
            contents.append(genai.types.Content(
                role="user" if role == "user" else "model",
                parts=[genai.types.Part.from_text(text=text)],
            ))
    contents.append(genai.types.Content(
        role="user",
        parts=[genai.types.Part.from_text(text=message)],
    ))
    return contents


def chat(message: str, history: list = None) -> str:
    """
    チャットメッセージに対してGeminiが回答を生成する。
    APIキーがない場合はローカル検索モードで応答する。

    Args:
        message: ユーザーのメッセージ
        history: 会話履歴 [("user", "..."), ("assistant", "...")]

    Returns:
        Geminiの回答文字列、またはローカル検索結果
    """
    genai = _get_genai()
    api_key = _get_api_key()
    if genai is None or not api_key:
        return _local_search(message)

    client = genai.Client(api_key=api_key)

    # Build tool_map from centralized registry
    from mcp_tools import get_tool_by_name, TOOL_REGISTRY
    tool_map = {t["name"]: get_tool_by_name(t["name"]) for t in TOOL_REGISTRY}

    tools = _make_tools(None, genai)
    contents = _build_contents(message, history, genai)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=contents,
            config=genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=4096,
                tools=tools,
            ),
        )
        return _process_gemini_response(response, tool_map, genai, client)
    except Exception as e:
        logger.exception("Gemini API error")
        return f"⛔ Gemini APIエラー: {str(e)[:200]}"


def _process_gemini_response(response, tool_map: dict, genai, client) -> str:
    """Process Gemini response, executing any tool calls."""
    parts = response.parts if hasattr(response, 'parts') else []

    tool_calls = []
    text_parts = []

    for part in parts:
        if hasattr(part, 'function_call') and part.function_call:
            fc = part.function_call
            tool_calls.append({
                "name": fc.name,
                "args": fc.args if isinstance(fc.args, dict) else {},
            })
        elif hasattr(part, 'text') and part.text:
            text_parts.append(part.text)

    if tool_calls:
        results = []
        for tc in tool_calls:
            func_name = tc["name"]
            args = tc["args"]

            if func_name not in tool_map:
                results.append(f"ツール {func_name} が見つかりません")
                continue

            func = tool_map[func_name]
            try:
                converted_args = {}
                for k, v in args.items():
                    if isinstance(v, str):
                        try:
                            parsed = json.loads(v)
                            converted_args[k] = parsed
                        except (json.JSONDecodeError, TypeError):
                            converted_args[k] = v
                    else:
                        converted_args[k] = v

                result = func(**converted_args)
                results.append(result)
            except Exception as e:
                logger.exception(f"Tool execution error: {func_name}")
                results.append(f"エラー: {func_name} の実行に失敗しました: {str(e)[:100]}")

        # Synthesize with Gemini
        follow_up_contents = []
        follow_up_contents.append(genai.types.Content(
            role="user",
            parts=[genai.types.Part.from_text(text="上記のツール呼び出しの結果を受けて回答してください。")],
        ))
        for i, tc in enumerate(tool_calls):
            follow_up_contents.append(genai.types.Content(
                role="tool",
                name=tc["name"],
                parts=[genai.types.Part.from_text(text=results[i])],
            ))

        follow_up = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=follow_up_contents,
            config=genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )

        return follow_up.text if hasattr(follow_up, 'text') and follow_up.text else "回答を生成できませんでした"

    return text_parts[0] if text_parts else "回答を生成できませんでした"
