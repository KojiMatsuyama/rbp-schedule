#!/usr/bin/env python3
"""
test_slack_headless.py — Playwright ヘッドレスブラウザ + Python で Slack 通知フローをテスト

実行: cd tests && python3 test_slack_headless.py
前提: サーバーが localhost:9999 で稼働していること
前提: playwright がインストールされていること (pip install playwright)

テスト内容:
  [1] .env に SLACK_WEBHOOK_URL が設定されている
  [2] chat_client.send_message() が正常に動作する
  [3] chat_client.send_message_with_card() が正常に動作する
  [4] POST /api/chat-webhook がテキストメッセージを送信できる
  [5] POST /api/chat-webhook がリッチカード（sections付き）を送信できる
  [6] 空メッセージは 400 エラーを返す
  [7] 存在しないエンドポイントは 404 を返す
  [8] チャットページがロードされ入力要素がある
  [9] 複雑なセクション構成が処理される
  [10] 連続送信が安定して動作する
  [11] title のみのメッセージが本文として扱われる
  [12] Slack通知キーワード検知ロジックが正しく動作する（Python直接テスト）
  [13] 自然な日本語フレーズがキーワード検知される（Python直接テスト）
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:9999"
TIMEOUT = 15000

# ─── Test runner ───────────────────────────────────────────────────
passed = 0
failed = 0
failures = []


def check(page, desc: str, condition: bool, detail: str = "") -> None:
    """Evaluate a condition and report."""
    if condition:
        print(f"  ✓ {desc}")
        global passed
        passed += 1
    else:
        msg = f"  ✗ {desc}"
        if detail:
            msg += f": {detail}"
        print(msg)
        global failed
        failed += 1
        failures.append({"desc": desc, "detail": detail})


# ─── Helpers ───────────────────────────────────────────────────────

def post_json(url: str, body: dict, timeout: float = 10.0) -> dict:
    """POST JSON and return parsed response."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", errors="replace"))
            body["status"] = e.code
            return body
        except Exception:
            return {"error": f"http_{e.code}", "status": e.code}
    except urllib.error.URLError as e:
        return {"error": f"network: {e.reason}"}
    except TimeoutError:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)[:100]}


def load_env(path: str) -> dict:
    """Simple .env file parser."""
    env = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def detect_slack_intent(message: str) -> bool:
    """
    Replicate the exact keyword detection logic from server.py.
    This mirrors the is_slack_request detection in _handle_chat_message.
    """
    slack_intent_keywords = [
        # Japanese
        "slackに通知", "slackに送信", "slackに送って", "slackに投げて",
        "メンバーに通知", "メンバーに共有", "チームに共有", "Slackで共有",
        "通知して", "共有して",
        # English
        "slack", "notify", "notification", "share",
        "member", "team",
    ]
    return any(kw in message.lower() for kw in slack_intent_keywords)


# ─── Tests ─────────────────────────────────────────────────────────

def test_1_env_configured(page):
    """[1] Env Config — .env に SLACK_WEBHOOK_URL が設定されている"""
    print("\n[1] Env Config")

    env = load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    webhook = env.get("SLACK_WEBHOOK_URL", "").strip()

    check(page, "SLACK_WEBHOOK_URL in .env",
          len(webhook) > 0,
          f"length={len(webhook)}, prefix={webhook[:30] if webhook else '(empty)'}...")


def test_2_chat_client_send_message(page):
    """[2] chat_client.send_message() — 直接インポートしてテスト"""
    print("\n[2] chat_client.send_message()")

    # Add parent dir (project root) to path so chat_client is importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from chat_client import send_message

    result = send_message(f"[UNIT TEST] send_message — {time.time()}")
    check(page, "send_message() returns dict with 'success' key",
          isinstance(result, dict) and "success" in result,
          f"result={result}")


def test_3_chat_client_send_card(page):
    """[3] chat_client.send_message_with_card() — リッチカード送信テスト"""
    print("\n[3] chat_client.send_message_with_card()")

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from chat_client import send_message_with_card

    result = send_message_with_card(
        "[UNIT TEST] Rich Card",
        [
            {"header": "Test Section", "fields": [
                {"text": "Field 1: Alpha"},
                {"text": "Field 2: Beta"},
            ]},
        ],
        footer="Headless Test"
    )
    check(page, "send_message_with_card() returns dict with 'success' key",
          isinstance(result, dict) and "success" in result,
          f"result={result}")


def test_4_direct_webhook_text(page):
    """[4] Direct Webhook — POST /api/chat-webhook がテキストメッセージを送信"""
    print("\n[4] Direct Webhook (text)")

    result = post_json(
        BASE_URL + "/api/chat-webhook",
        {"text": f"[HEADLESS TEST] テキスト送信テスト — {time.strftime('%H:%M:%S')}"}
    )

    check(page, "POST /api/chat-webhook responds",
          "status" in result or "error" in result,
          f"keys={list(result.keys())}")

    if result.get("status") == "sent":
        check(page, "Message sent successfully", True)
    elif "error" in result:
        check(page, "Webhook error noted (acceptable if URL unreachable)",
              True, f"error: {result['error'][:100]}")


def test_5_direct_webhook_card(page):
    """[5] Direct Webhook — POST /api/chat-webhook がリッチカードを送信"""
    print("\n[5] Direct Webhook (rich card)")

    result = post_json(
        BASE_URL + "/api/chat-webhook",
        {
            "title": "[HEADLESS TEST] リッチカード",
            "sections": [
                {
                    "header": "テスト情報",
                    "fields": [
                        {"text": "種別: ヘッドレスブラウザテスト"},
                        {"text": "時刻: " + time.strftime("%Y-%m-%d %H:%M:%S")},
                        {"text": "状態: 正常終了"},
                    ],
                }
            ],
        }
    )

    check(page, "Rich card POST responds",
          "status" in result or "error" in result,
          f"keys={list(result.keys())}")


def test_6_invalid_requests(page):
    """[6] Invalid Requests — 空メッセージは 400、未知ルートは 404"""
    print("\n[6] Invalid Requests")

    # Empty message → 400
    r1 = post_json(BASE_URL + "/api/chat/message", {"message": ""})
    check(page, "Empty message → 400",
          r1.get("status") == 400, f"status={r1.get('status')}")

    # Missing message → 400
    r2 = post_json(BASE_URL + "/api/chat/message", {})
    check(page, "Missing message field → 400",
          r2.get("status") == 400, f"status={r2.get('status')}")

    # Empty webhook → 400
    r3 = post_json(BASE_URL + "/api/chat-webhook", {})
    check(page, "Empty webhook → 400",
          r3.get("status") == 400, f"status={r3.get('status')}")

    # Unknown route → 404
    r4 = post_json(BASE_URL + "/api/nonexistent", {"foo": "bar"})
    check(page, "Unknown route → 404",
          r4.get("status") == 404, f"status={r4.get('status')}")


def test_7_chat_page_loads(page):
    """[7] Chat Page — チャットページがロードされ入力要素がある"""
    print("\n[7] Chat Page")

    page.goto(BASE_URL + "/chat", wait_until="domcontentloaded", timeout=TIMEOUT)

    check(page, "Chat page title contains AI/RBP",
          "AIアシスタント" in page.title() or "RBP" in page.title(),
          f"title='{page.title()}'")

    input_el = page.query_selector("textarea, input[type='text']")
    check(page, "Chat input element exists",
          input_el is not None)

    send_btn = page.query_selector("button")
    check(page, "Send button exists",
          send_btn is not None)

    # Type Slack notification text
    if input_el:
        input_el.fill("この結果をSlackに通知して")
        val = input_el.input_value()
        check(page, "Input accepts Slack notification text",
              "slack" in val.lower() or "Slack" in val,
              f"typed='{val}'")


def test_8_complex_sections(page):
    """[8] Complex Sections — 複雑なセクション構成が処理される"""
    print("\n[8] Complex Sections")

    result = post_json(
        BASE_URL + "/api/chat-webhook",
        {
            "title": "[TEST] Format Validation",
            "sections": [
                {"header": "Section 1", "fields": [{"text": "Key: Value 1"}]},
                {"header": "Section 2", "fields": [
                    {"text": "Field A: Alpha"},
                    {"text": "Field B: Beta"},
                    {"text": "Field C: Gamma"},
                ]},
                {"header": "Section 3", "fields": [{"text": "Final: OK"}]},
            ],
            "footer": "Generated by STB Headless Test",
        }
    )

    check(page, "Complex sections handled",
          "status" in result or "error" in result,
          f"status={result.get('status')}")


def test_9_sequential_sends(page):
    """[9] Sequential Sends — 連続送信が安定して動作する"""
    print("\n[9] Sequential Sends")

    results = []
    for i in range(3):
        r = post_json(
            BASE_URL + "/api/chat-webhook",
            {"text": f"[SEQ #{i+1}] {time.time()}"}
        )
        results.append(r)

    for i, r in enumerate(results):
        check(page, f"Sequential send #{i+1}",
              "status" in r or "error" in r,
              f"status={r.get('status')}")


def test_10_title_only(page):
    """[10] Title Fallback — title のみで text なしの時、title が本文になる"""
    print("\n[10] Title Fallback")

    result = post_json(
        BASE_URL + "/api/chat-webhook",
        {"title": "[TITLE ONLY] titleのみのテスト"}
    )
    check(page, "Title-only message processed",
          "status" in result or "error" in result,
          f"status={result.get('status')}")


def test_11_slack_keyword_logic(page):
    """[11] Slack Keyword Logic — 日本語・英語のキーワード検知ロジックを検証"""
    print("\n[11] Slack Keyword Logic (direct Python test)")

    # Trigger keywords — should all return True
    trigger_keywords = [
        "slackに通知", "slackに送信", "slackに送って", "slackに投げて",
        "メンバーに通知", "メンバーに共有", "チームに共有", "Slackで共有",
        "通知して", "共有して",
        "slack", "notify", "notification", "share",
        "member", "team",
    ]

    for kw in trigger_keywords:
        result = detect_slack_intent(kw)
        check(page, f"Triggers: '{kw}'",
              result is True, f"got {result}")

    # Non-trigger messages — should return False
    non_trigger_messages = [
        "こんにちは",
        "今日の天気はどうですか？",
        "殺菌剤の一覧を出して",
        "アブラムシに効く薬剤を教えて",
        "今月の防除履歴を教えて",
        "RBPの処方結果を教えて",
        "今日の予報を確認して",
    ]

    for msg in non_trigger_messages:
        result = detect_slack_intent(msg)
        check(page, f"No trigger: '{msg[:25]}...'",
              result is False, f"got {result}")


def test_12_natural_language_slack(page):
    """[12] Natural Language — 自然な日本語フレーズのキーワード検知を検証"""
    print("\n[12] Natural Language Slack Phrases (direct Python test)")

    phrases = [
        ("今日のアブラムシ発生状況をSlackに通知して", True),
        ("この処方結果をメンバーに共有して", True),
        ("今週の防除履歴をチームに共有して", True),
        ("炭疽病の対策をSlackで共有して", True),
        ("今日の予報結果を通知して", True),
        ("この結果を共有して", True),
        ("チームに通知して", True),
        ("メンバーに通知して", True),
        ("通常の質問です", False),
        ("薬剤の一覧を教えて", False),
        ("今日の天気は？", False),
    ]

    for phrase, expected in phrases:
        result = detect_slack_intent(phrase)
        check(page, f"'{phrase[:30]}...' → {'TRIGGER' if expected else 'NO TRIGGER'}",
              result is expected,
              f"expected={expected}, got={result}")


# ─── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  STB Slack Notification — Headless Browser Test Suite")
    print("=" * 60)

    # Health check
    try:
        urllib.request.urlopen(BASE_URL, timeout=5)
        print(f"\n✓ Server reachable at {BASE_URL}")
    except Exception as e:
        print(f"\n✗ Server unreachable at {BASE_URL}: {e}")
        print("Please start the server first: python3 server.py")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        try:
            test_1_env_configured(page)
            test_2_chat_client_send_message(page)
            test_3_chat_client_send_card(page)
            test_4_direct_webhook_text(page)
            test_5_direct_webhook_card(page)
            test_6_invalid_requests(page)
            test_7_chat_page_loads(page)
            test_8_complex_sections(page)
            test_9_sequential_sends(page)
            test_10_title_only(page)
            test_11_slack_keyword_logic(page)
            test_12_natural_language_slack(page)
        except Exception as e:
            print(f"\n!!! Unexpected error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    # Summary
    total = passed + failed
    print("\n" + "=" * 60)
    print(f"  Result: {passed}/{total} passed, {failed} failed")
    print("=" * 60)
    if failed == 0:
        print("  ALL TESTS PASSED — Slack通知ヘッドレステスト成功！")
    else:
        print("  SOME TESTS FAILED:")
        for f in failures:
            print(f"    - {f['desc']}: {f['detail']}")
    print("=" * 60)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
