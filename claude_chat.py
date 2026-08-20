#!/usr/bin/env python3
"""
claude_chat.py — Claude API (Anthropic SDK) を使ったチャットバックエンド

mcp_tools.py の関数を Claude のツールとして呼び出す。
server.py から import して使う。
Anthropic SDK + LiteLLM プロキシ対応。
"""

import json
import os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import — only when needed
_anthropic = None


def _get_anthropic():
    global _anthropic
    if _anthropic is None:
        try:
            import anthropic as a
            _anthropic = a
        except ImportError:
            logger.error("anthropic がインストールされていません。pip install anthropic を実行してください。")
            return None
    return _anthropic


def _get_api_key() -> Optional[str]:
    """Load ANTHROPIC_API_KEY from environment or .env file."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
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
                if k.strip() == "ANTHROPIC_API_KEY" and v.strip():
                    return v.strip()
    return None


def _get_base_url() -> str:
    """Get Anthropic-compatible API base URL (LiteLLM proxy)."""
    return os.environ.get("ANTHROPIC_BASE_URL", "http://192.168.131.161:24200")


def _get_model() -> str:
    """Get model name."""
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")


def _is_local_llm_available() -> bool:
    """Check if the local LLM (Qwen3.6-35B) is reachable via LiteLLM."""
    try:
        import requests
        resp = requests.get(
            f"{_get_base_url()}/models",
            headers={"x-litellm-api-key": "Bearer sk-litellm-test-1234"},
            timeout=5,
        )
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            return any(m["id"] == "local-llm" for m in models)
    except Exception:
        pass
    return False


def _call_local_llm(messages: list, system: str, tools: list, tool_map: dict, max_tokens: int = 2048) -> str:
    """
    Call the local LLM (Qwen3.6-35B) via LiteLLM's OpenAI-compatible endpoint.

    Supports tool calling: detects tool_use responses, executes tools locally,
    feeds results back, and repeats until the model produces a final answer.

    Handles reasoning-only models by aggressively stripping reasoning content.

    Guards against infinite tool-calling loops:
    - Hard cap on total iterations (MAX_ITERATIONS)
    - Force-terminate after MAX_TOOL_CHAIN consecutive tool-use responses
      (typically 2 tool calls are enough; 3+ means the model is stuck)
    - Track tool names to detect repeated single-tool loops

    Args:
        messages: OpenAI-style conversation messages
        system: System prompt
        tools: OpenAI-format tool definitions (for API)
        tool_map: Dict mapping tool name -> callable function (for execution)
        max_tokens: Max tokens in response
    """
    import requests

    url = f"{_get_base_url()}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "x-litellm-api-key": "Bearer sk-litellm-test-1234",
    }

    # Build OpenAI-style messages
    openai_messages = []
    if system:
        openai_messages.append({"role": "system", "content": system})
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            openai_messages.append({"role": "user", "content": content})
        elif role == "assistant":
            openai_messages.append({"role": "assistant", "content": content})
        elif role == "tool":
            openai_messages.append({"role": "tool", "content": content})

    # tools is already in OpenAI format from convert_tools_to_openai_format()
    openai_tools = tools

    # Tool execution loop — repeat until the model gives a text answer
    MAX_ITERATIONS = 10
    MAX_TOOL_CALLS = 2  # max tool-use responses before forcing stop
    consecutive_tool_calls = 0
    force_no_tools = False  # when True, remove tools from payload to force text answer
    for iteration in range(MAX_ITERATIONS):
        payload = {
            "model": "local-llm",
            "messages": openai_messages,
            "tools": openai_tools if openai_tools and not force_no_tools else None,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()

            choices = data.get("choices", [])
            if not choices:
                return "回答を生成できませんでした"

            message = choices[0].get("message", {})

            # --- Check for tool_use ---
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                # Guard: too many consecutive tool-use responses
                if consecutive_tool_calls >= MAX_TOOL_CALLS:
                    logger.warning(
                        f"Max tool calls ({MAX_TOOL_CALLS}) exceeded at iter {iteration}. "
                        f"Forcing answer by removing tools."
                    )
                    # Summarize accumulated tool results for the model
                    tool_summaries = []
                    for tr in tool_results:
                        tool_summaries.append(f"[{tr['name']}] {tr['content'][:500]}")
                    summary_text = "\n\n".join(tool_summaries) if tool_summaries else "(ツール結果なし)"

                    # Inject stop prompt WITH tool results so model can answer from data
                    openai_messages.append({
                        "role": "user",
                        "content": (
                            f"これまでに取得したツール結果:\n{summary_text}\n\n"
                            "これらの結果を使って、必ずユーザーへの最終回答を生成してください。"
                            "それ以上ツールを呼び出さないでください。結論を述べてください。"
                        ),
                    })
                    force_no_tools = True
                    continue  # Re-call WITHOUT tools

                # Execute each tool call
                tool_results = []
                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    fn_args_raw = tc.get("function", {}).get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_raw)
                    except (json.JSONDecodeError, TypeError):
                        fn_args = {}

                    func = tool_map.get(fn_name)
                    if func is None:
                        tool_results.append({
                            "tool_call_id": tc.get("id", ""),
                            "name": fn_name,
                            "content": f"ツール {fn_name} が見つかりません",
                        })
                        continue

                    try:
                        converted_args = {}
                        for k, v in fn_args.items():
                            if isinstance(v, str):
                                try:
                                    converted_args[k] = json.loads(v)
                                except (json.JSONDecodeError, TypeError):
                                    converted_args[k] = v
                            else:
                                converted_args[k] = v

                        result = func(**converted_args)
                        tool_results.append({
                            "tool_call_id": tc.get("id", ""),
                            "name": fn_name,
                            "content": result,
                        })
                    except Exception as e:
                        logger.exception(f"Tool execution error: {fn_name}")
                        tool_results.append({
                            "tool_call_id": tc.get("id", ""),
                            "name": fn_name,
                            "content": f"エラー: {fn_name} の実行に失敗しました: {str(e)[:100]}",
                        })

                consecutive_tool_calls += 1

                # Append tool results to messages
                for tr in tool_results:
                    openai_messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": tr["tool_call_id"],
                            "type": "function",
                            "function": {
                                "name": tr["name"],
                                "arguments": json.dumps(tr.get("_original_args", {}), ensure_ascii=False),
                            },
                        }],
                    })
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "content": tr["content"],
                    })
                continue  # Loop back to call the model again

            # --- No tool_use — extract final answer ---
            content = message.get("content")
            if not content:
                content = message.get("reasoning_content", "")

            # Aggressively strip reasoning/thinking content
            if content:
                content = _strip_reasoning(content)

            if content:
                return content

            return "回答を生成できませんでした"

        except requests.exceptions.Timeout:
            return "⛔ ローカルLLMがタイムアウトしました。サーバーが応答しているか確認してください。"
        except requests.exceptions.ConnectionError:
            return "⛔ ローカルLLMサーバーに接続できません。192.168.131.161:24200 が応答しているか確認してください。"
        except Exception as e:
            logger.exception("Local LLM error")
            return f"⛔ ローカルLLMエラー: {str(e)[:200]}"

    return "⛔ 応答生成中にエラーが発生しました（最大反復回数を超えました）"


def _strip_reasoning(content: str) -> str:
    """Aggressively strip reasoning/thinking content from LLM output."""
    if not content:
        return ""

    # 0. Remove "Here's a thinking process:" and similar intro lines
    content = re.sub(
        r'^(?:Here\'s a thinking process:?|Let me think about this|Let me analyze this|Okay, let me|Alright, let me|Hmm, let me|Wait, let me|So, let me|First, I need to|First, I should|I need to figure out|I should check|Let me check|Let me look up|Let me search|Let me retrieve|Let me query|Based on my knowledge|According to my training|From what I know|I\'m aware that|I\'m familiar with)\s*\n?',
        '',
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # 0.5. Remove Chinese/Japanese reasoning intros
    content = re.sub(
        r'^(?:让我|我认为|我需要|我应该|首先|其次|然后|接下来|最后|总之|综上所述|根据我的理解|我了解到|我知道|我记得|我想|我觉得|分析一下|思考一下|考虑一下)\s*[。,.！？]',
        lambda m: m.group(0)[0] + '\n',  # keep first char, rest becomes newline
        content,
        flags=re.MULTILINE,
    )

    # 1. Remove <think>...</think> blocks (greedy — handles multi-line)
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)

    # 1.5. Also handle nested or malformed reasoning tags
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)

    # 2. Remove "Thinking Process:", "Thought:", etc.
    content = re.sub(
        r'(?:^|\n)\s*(?:Thinking\s+(?:Process|Steps|Process:)|Thought(?:s)?\s*:?)\s*(?:\n|$)',
        '\n',
        content,
        flags=re.IGNORECASE,
    )

    # 3. Remove numbered reasoning steps at the start (1. **Analyze**, 2. **Identify**, ...)
    content = re.sub(
        r'^\s*\d+\.\s+\*\*[A-Z][^\*]*\*\*\s*\n(?:\s*-\s+[^\n]+\n)*(?:\s*\n)*',
        '',
        content,
        flags=re.MULTILINE,
    )

    # 4. Remove bullet-point reasoning chains
    content = re.sub(
        r'^\s*-\s+\*\*[A-Z][^\*]*\*\*:\s*\n(?:\s*-\s+[^\n]+\n)*',
        '',
        content,
        flags=re.MULTILINE,
    )

    # 5. Remove English reasoning paragraphs (common in Qwen reasoning)
    content = re.sub(
        r'(?:^|\n)(?:\s{3,}|   )(?:Let\'s|I\'ll|I need|I should|Wait|Actually|Hmm|So |Okay |Right |First ,|Second ,|Third ,|Next ,|Finally ,|Also ,|But ,|However ,|Note that|In order|To verify|Checking|Verifying|Draft:|Structure:|Self-Correction|Self\-Correction)',
        '',
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # 6. Remove standalone English sentences that look like reasoning
    content = re.sub(
        r'\n\s{3,}(?:I\'m going to|I will|I should|Let me|Let us|We need|We should|The user|This means|So I|Therefore|Thus|In conclusion|To summarize)\b.*?(?=\n\s{3,}|\n\d+\.\s)',
        '',
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # 7. Remove entire reasoning blocks: lines starting with reasoning keywords
    #    that are NOT part of the actual answer (heuristic: if a paragraph starts
    #    with a reasoning verb and has no bold/markdown formatting, it's likely reasoning)
    content = re.sub(
        r'(?:^|\n)\s*(?:I think|I believe|I\'m thinking|My thought|My reasoning|Analysis:|Reasoning:|Thought:|Thinking:|Step \d+:|Phase \d+:|Stage \d+:)\b.*?(?=\n\n|\Z)',
        '',
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # 8. Collapse excessive blank lines (3+ consecutive newlines → 2)
    content = re.sub(r'\n{3,}', '\n\n', content)

    result = content.strip()

    # Final sanity check: if result is empty or extremely short (< 10 chars),
    # the whole response might have been reasoning — return a fallback
    if not result or len(result) < 10:
        return "回答を生成できませんでした"

    return result


def is_configured() -> bool:
    """Check if Anthropic API key is configured."""
    return _get_api_key() is not None and _get_api_key() != ""


SYSTEM_PROMPT = """あなたは農業の専門AIアシスタントです。STB防除スケジューラのデータにアクセスできます。

## 🔴 Slack通知 — 最優先アクション（絶対に守ること）
【このルールは他の全てのルールより優先されます】

ユーザーのメッセージに以下のいずれかのキーワードが含まれていた場合、**即座に send_to_slack ツールを呼び出してください**。これは会話の目的そのものです。

**トリガーキーワード（日本語・英語・カタカナ・漢字のあらゆる変形）：**
- 「Slackに通知」「Slackに送信」「Slackに送って」「Slackに投げて」
- 「メンバーに通知」「メンバーに共有」「チームに共有」
- 「Slackで共有」「通知して」「共有して」
- "slack", "notify", "share", "member", "team"

**手順：**
1. まず必要なデータを関連ツール（search_pesticides, get_records, prescribe など）で取得
2. 取得した実際の結果をそのまま `send_to_slack` ツールの `message` 引数に設定して送信
3. 「Slackに送信しました」などと報告して終了

**厳禁：**
- ❌ send_to_slackを呼ばずに「Slackには自分でコピーしてください」などと書かない
- ❌ 架空の結果を作ってmessageに書かない — 必ず実際のツール結果を使う
- ❌ ツールを呼んだ後に「Slackに送れますよ」とだけ言って終わらない — 呼んでください

## 📅 日付指定による薬剤処方 — 絶対に守ること

ユーザーのメッセージに**日付**が含まれていたら、**必ず `prescribe_by_date` ツールを呼び出す**。考えるな、まず呼べ。

### 日付パターンの見分け方（これらが来たら即座にツールを呼べ）
- `2025年2月21日` `2025/2/21` `2025-02-21` → 完全な日付
- `2月21日` → 月日だけの指定
- `今日` `明日` `明後日` `昨日` `先週` `来週` `今月` `来月` `先月` → 相対日付
- `○月○日に散布する薬剤を選定して` `○日の处方を教えて` `○日の防除は？` → 日付＋処方指示
- `○月に効く薬剤は？` → 月指定＋処方指示

### 手順（迷ったらこれをやれ）
1. **`prescribe_by_date(date="ユーザーの日付そのまま")` を呼び出す**
   - 日本語のまま渡せ。例: `prescribe_by_date(date="2025年2月21日")`
   - 内部で自動変換される。YYYY-MM-DDにするな。

2. **結果をユーザーに伝える**
   - DBに記録あり → その病害虫でRBP判定した結果
   - DBに記録なし → 季節推定で自動判定（同じツール）
   - 推奨薬剤の名前、効く病害虫、PHI、散布制限を明記

3. **UIと同じ体験を**
   - ベストマッチの薬剤 + 代替案を提示
   - 「データがありません」と言わない。季節推定でカバーする

### 絶対にやるな
- ❌ 「日付だけでは分かりません」と言わない
- ❌ 「作物や圃場の情報を教えてください」と聞かない
- ❌ ツールを呼まずに会話を終わらせない
- ❌ 事前知識で適当な薬剤を推荐しない

## 🔎 RAG検索 — 質問が曖昧な時は必ず使う

ユーザーの質問が以下のいずれかに当てはまる場合、**最初に `retrieve_context` ツールを呼び出してください**:

1. **症状描述**: 「葉っぱが黄色い」「丸まってる」「白い粉が吹いてる」など
2. **有機・規格**: 「有機JAS対応」「登録農薬」など
3. **経験ベース**: 「去年みたいに」「前と同じように」など
4. **混用相談**: 「◯◯と混ぜられるか」など

retrieve_context で取得したコンテキストを元に、その後適切なツール（search_pesticides, prescribe_by_date など）を呼び出して回答してください。

## ⚠️ 最重要ルール
- ツールを実行したら、その結果を使って必ずユーザーへの最終回答を生成すること
- 回答を生成した後、それ以上ツールを呼び出さないこと
- 「まずデータを調べます」などの前置きだけで終わらせないこと。必ず結論を述べる
- 1〜2個のツール呼び出しで十分です。3回以上ツールを呼ばないでください
- わからないことは「データがありません」と正直に言う

## その他のルール
- 必ず適切なツールを呼び出して、データベースからリアルタイムのデータを取得してから回答してください
- ツールで取得したデータに基づいて回答し、推測や事前知識だけで答えない
- 薬剤の推奨には常にPHI（収穫待機日）、散布回数、毒性区分を明記する
- 過剰な保証や責任ある医療助言のような表現は避ける
- データに基づく客観的な情報を提供することを心がける
- 複数回のツール呼び出しが必要な場合は、順番に実行して統合した回答をする
- 日本語で回答する
"""


def _local_search(message: str) -> str:
    """Claudeなしのローカル検索モード。DBからデータを引っ張って整形して返す。"""
    from mcp_tools import (
        search_pesticides,
        list_pesticides,
        list_diseases,
        get_records,
        summarize_history,
        get_current_season_advice,
    )

    msg = message.lower()

    # --- 病害虫名での検索（炭疽病、うどんこ病、アブラムシなど） ---
    # ★ 優先度高：「〇〕に効く薬剤」「〇〕の対策」などで特定病害虫に限定検索
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
        return get_records(limit=50)

    # --- 薬剤一覧 ---
    if "薬剤" in msg or "一覧" in msg or "全部" in msg or "すべて" in msg:
        return list_pesticides(limit=100)

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
        f"※ Anthropic APIキーを設定すると、より高度なAI回答が得られます。"
    )


def _make_tools(selected_tools=None):
    """
    Build Claude-format tool definitions from TOOL_REGISTRY.

    Args:
        selected_tools: ツール名のリスト（None で全ツール）
    """
    from mcp_tools import TOOL_REGISTRY

    if selected_tools is None:
        registry = TOOL_REGISTRY
    else:
        registry = [t for t in TOOL_REGISTRY if t["name"] in selected_tools]

    tools = []
    for t in registry:
        schema = t["input_schema"]
        props = schema.get("properties", {})
        # Claude format: flatten properties (remove nested "type":"object")
        flat_props = {}
        for k, v in props.items():
            flat_props[k] = v
        tools.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": {
                "type": "object",
                "properties": flat_props,
                "required": schema.get("required", []),
            },
        })
    return tools


def _build_messages(message: str, history: list) -> list:
    """Build conversation messages list for Anthropic API."""
    messages = []
    if history:
        for role, text in history[-10:]:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": message})
    return messages


def chat(message: str, history: list = None, is_slack_request: bool = False) -> str:
    """
    チャットメッセージに対してAIが回答を生成する。

    優先順位:
    1. Anthropic APIキーがあれば Claude API（ツール呼び出し対応）
    2. ローカルLLM（Qwen3.6-35B）が利用可能ならそちらを使用
    3. いずれもない場合はローカル検索モード

    Args:
        message: ユーザーのメッセージ
        history: 会話履歴 [("user", "..."), ("assistant", "...")]
        is_slack_request: Trueの場合、メッセージはSlack通知を求めていることを示す
            フラグ。Trueのとき、メッセージ末尾に文脈ヒントを自動付与する。

    Returns:
        AIの回答文字列
    """
    anthropic = _get_anthropic()
    api_key = _get_api_key()

    # Priority 1: Claude API
    if anthropic is not None and api_key:
        return _chat_with_claude(message, history, is_slack_request)

    # Priority 2: Local LLM (Qwen3.6-35B)
    if _is_local_llm_available():
        return _chat_with_local_llm(message, history, is_slack_request)

    # Priority 3: Keyword-based local search fallback
    return _local_search(message)


def _chat_with_claude(message: str, history: list, is_slack_request: bool = False) -> str:
    """Chat using Claude API with tool support."""
    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=_get_base_url(),
    )

    # Build tool_map from centralized registry
    from mcp_tools import get_tool_by_name, TOOL_REGISTRY
    tool_map = {t["name"]: get_tool_by_name(t["name"]) for t in TOOL_REGISTRY}

    tools = _make_tools()

    # If this is a Slack notification request, append a hint so the model
    # knows to use send_to_slack instead of just talking about it.
    if is_slack_request:
        message = message + (
            "\n\n⚠️ 重要: このリクエストはSlack通知を求めています。"
            "必ず send_to_slack ツールを呼び出して、"
            "直前の会話で取得した実際の結果を message パラメータに書いて送信してください。"
        )

    messages = _build_messages(message, history)

    try:
        response = client.messages.create(
            model=_get_model(),
            system=SYSTEM_PROMPT,
            messages=messages,
            max_tokens=4096,
            temperature=0.3,
            tools=tools,
        )
        return _process_claude_response(response, tool_map, client, messages)
    except Exception as e:
        logger.exception("Claude API error")
        return f"⛔ Claude APIエラー: {str(e)[:200]}"


def _chat_with_local_llm(message: str, history: list, is_slack_request: bool = False) -> str:
    """Chat using local LLM (Qwen3.6-35B) via LiteLLM with tool support."""
    from mcp_tools import get_tool_by_name, TOOL_REGISTRY, convert_tools_to_openai_format

    # Build tool_map from registry
    tool_map = {t["name"]: get_tool_by_name(t["name"]) for t in TOOL_REGISTRY}

    # Build OpenAI-format tool definitions
    tools = convert_tools_to_openai_format(TOOL_REGISTRY)

    # If this is a Slack notification request, append a hint so the model
    # knows to use send_to_slack instead of just talking about it.
    if is_slack_request:
        message = message + (
            "\n\n⚠️ 重要: このリクエストはSlack通知を求めています。"
            "必ず send_to_slack ツールを呼び出して、"
            "直前の会話で取得した実際の結果を message パラメータに書いて送信してください。"
        )

    # Build conversation context
    msgs = []
    if history:
        for role, text in history[-10:]:
            msgs.append({"role": role, "content": text})
    msgs.append({"role": "user", "content": message})

    return _call_local_llm(msgs, SYSTEM_PROMPT, tools, tool_map, max_tokens=8192)


def _process_claude_response(response, tool_map: dict, client, messages) -> str:
    """Process Claude response, executing any tool calls in a loop with guards."""
    MAX_ITERATIONS = 10
    MAX_TOOL_CHAIN = 3
    consecutive_tool_calls = 0
    prev_tool_names = []

    for iteration in range(MAX_ITERATIONS):
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "name": block.name,
                    "args": block.input if isinstance(block.input, dict) else {},
                })

        if text_parts and not tool_calls:
            return text_parts[0]

        if tool_calls:
            # Guard: repeated single-tool loop
            current_tool_names = [tc["name"] for tc in tool_calls]
            if len(current_tool_names) == 1 and consecutive_tool_calls > 0:
                if current_tool_names[0] in prev_tool_names:
                    logger.warning(
                        f"Claude infinite tool loop: '{current_tool_names[0]}' "
                        f"called {consecutive_tool_calls + 1} times. Forcing stop."
                    )
                    return (
                        f"⚠️ 同じツール('{current_tool_names[0]}')が繰り返し呼び出されました。"
                        f"これ以上のツール呼び出しは停止します。\n\n"
                        f"この問題が続く場合は、別の質問をお試しください。"
                    )

            # Guard: too many consecutive tool-use responses
            if consecutive_tool_calls >= MAX_TOOL_CHAIN:
                logger.warning(
                    f"Claude max tool chain ({MAX_TOOL_CHAIN}) exceeded at iteration {iteration}. "
                    f"Forcing final answer."
                )
                return (
                    "⚠️ 複数のツールを呼び出しましたが、これ以上のツール呼び出しは停止します。\n\n"
                    "以下はこれまでの情報をもとにした回答です："
                )

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

            # Add tool results to messages
            for i, tc in enumerate(tool_calls):
                messages.append({
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"toolu_{i}",
                            "name": tc["name"],
                            "input": tc["args"],
                        }
                    ],
                })
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"toolu_{i}",
                            "content": results[i],
                        }
                    ],
                })

            consecutive_tool_calls += 1
            prev_tool_names = current_tool_names

            # Call again
            response = client.messages.create(
                model=_get_model(),
                system=SYSTEM_PROMPT,
                messages=messages,
                max_tokens=4096,
                temperature=0.3,
            )
            continue

        # Neither text nor tool_use — shouldn't happen but safety net
        return "回答を生成できませんでした"

    return "⛔ 応答生成中にエラーが発生しました（最大反復回数を超えました）"
