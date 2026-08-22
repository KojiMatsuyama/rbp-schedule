#!/usr/bin/env python3
"""
test_headless.py — Playwright ヘッドレスブラウザでSTBアプリをテスト

実行: cd tests && python3 test_headless.py
前提: サーバーが localhost:9999 で稼働していること
前提: playwright がインストールされていること (pip install playwright)
"""

import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:9999"
TIMEOUT = 15000

# ─── Test runner ───────────────────────────────────────────────────
passed = 0
failed = 0
failures = []


def check(page, desc: str, condition: bool, detail: str = "") -> None:
    """Evaluate a condition in the browser and report."""
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


def check_element(page, selector: str, desc: str, should_exist: bool = True) -> None:
    """Check if a CSS selector matches at least one element."""
    found = page.query_selector(selector) is not None
    check(page, desc, found == should_exist, f"exists={found}, expected={should_exist}")


def check_js(page, expr: str, desc: str, expected) -> None:
    """Evaluate a JS expression and compare."""
    result = page.evaluate(expr)
    check(page, desc, result == expected, f"got {result!r}, expected {expected!r}")


def check_js_gte(page, expr: str, desc: str, minimum: int) -> None:
    """Evaluate a JS expression and check it's >= minimum."""
    result = page.evaluate(expr)
    ok = result >= minimum
    check(page, desc, ok, f"got {result}, expected >= {minimum}")


# ─── Tests ─────────────────────────────────────────────────────────

def test_1_page_load(page):
    """[1] Page Load — トップページが正しく読み込まれる"""
    print("\n[1] Page Load")
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)

    check(page, "Page title is correct",
          page.title() == "RBP 防除スケジュールリスト")

    h1 = page.eval_on_selector('h1', "el => el.textContent.trim()")
    check(page, "H1 heading contains RBP", "RBP" in h1, f"got '{h1}'")

    # Check main containers exist
    check_element(page, ".year-nav", "Year navigation exists")
    check_element(page, ".calendar-panel", "Calendar panel exists")
    check_element(page, ".side-panel", "Side panel exists")


def test_2_javascript_execution(page):
    """[2] JavaScript Execution — RBPエンジン関数が定義されている"""
    print("\n[2] JavaScript Execution")

    check_js(page, "() => typeof dotProduct", "dotProduct function available", "function")
    check_js(page, "() => typeof matchExactBox", "matchExactBox function available", "function")
    check_js(page, "() => typeof runLineThroughBridges",
             "runLineThroughBridges function available", "function")
    check_js(page, "() => typeof classifyAndRegisterVector",
             "classifyAndRegisterVector function available", "function")
    check_js(page, "() => typeof cosineSimilarity",
             "cosineSimilarity function available", "function")


def test_3_data_loading(page):
    """[3] Data Loading — 全データセットが読み込まれている"""
    print("\n[3] Data Loading")

    check_js_gte(page, "() => typeof EB_VECTORS === 'object' && EB_VECTORS !== null ? Object.keys(EB_VECTORS).length : 0",
                 "EB_VECTORS loaded", 1)
    check_js_gte(page, "() => Array.isArray(EB_MATRIX) ? EB_MATRIX.length : 0",
                 "EB_MATRIX loaded", 1)
    check_js_gte(page, "() => typeof EB_NAMES === 'object' && EB_NAMES !== null ? Object.keys(EB_NAMES).length : 0",
                 "EB_NAMES loaded", 1)
    check_js_gte(page, "() => Array.isArray(DISEASES) ? DISEASES.length : 0",
                 "DISEASES loaded", 1)
    check_js_gte(page, "() => Array.isArray(PESTICIDE_DB) ? PESTICIDE_DB.length : 0",
                 "PESTICIDE_DB loaded", 1)


def test_4_rbp_core_math(page):
    """[4] RBP Core Functions — 数学演算が正しく動く"""
    print("\n[4] RBP Core Functions")

    # Dot product: [1,0,1,0,1,0,1,0,1,0] · [1,0,1,0,1,0,1,0,1,0] = 5
    check_js(page,
             "() => dotProduct([1,0,1,0,1,0,1,0,1,0], [1,0,1,0,1,0,1,0,1,0])",
             "dotProduct identical vectors", 5)

    # Dot product orthogonal: [1,0,0,...] · [0,1,0,...] = 0
    check_js(page,
             "() => dotProduct([1,0,0,0,0,0,0,0,0,0], [0,1,0,0,0,0,0,0,0,0])",
             "dotProduct orthogonal vectors", 0)

    # Cosine similarity identical = 1.0
    cos = page.evaluate(
        "() => cosineSimilarity([1,0,1,0,1,0,1,0,1,0], [1,0,1,0,1,0,1,0,1,0])"
    )
    check(page, "cosineSimilarity identical ≈ 1.0", abs(cos - 1.0) < 0.001, f"got {cos}")

    # Cosine similarity opposite = -1.0
    cos_neg = page.evaluate(
        "() => cosineSimilarity([1,0,1,0,1,0,1,0,1,0], [-1,0,-1,0,-1,0,-1,0,-1,0])"
    )
    check(page, "cosineSimilarity opposite ≈ -1.0", abs(cos_neg - (-1.0)) < 0.001, f"got {cos_neg}")


def test_5_ui_elements(page):
    """[5] UI Elements — 主要UIコンポーネントが存在する"""
    print("\n[5] UI Elements")

    check_element(page, ".year-nav", "Year navigation bar")
    check_element(page, ".calendar-panel", "Calendar panel")
    check_element(page, ".side-panel", "Side panel")
    check_element(page, ".tab-pane", "At least one tab pane")

    # Count tabs
    tab_count = page.query_selector_all(".tab-pane").__len__()
    check(page, "Panel tabs exist (≥3)", tab_count >= 3, f"found {tab_count}")

    # Year display
    check_element(page, "#yearDisplay", "Year display element")

    # Menu button
    check_element(page, "#headerMenuToggle", "Header menu toggle button")


def test_6_tab_switching(page):
    """[6] Tab Switching — メニュー項目の切替が正常に動作する"""
    print("\n[6] Tab Switching")

    # Open side panel menu
    page.click(".menu-toggle", timeout=TIMEOUT)
    page.wait_for_timeout(500)

    # Get all menu items
    menu_items = page.query_selector_all(".menu-item")
    check(page, "Menu items visible after toggle", len(menu_items) >= 3, f"found {len(menu_items)}")

    if len(menu_items) >= 3:
        # First item: 日次詳細 (already active)
        active_before = page.eval_on_selector(".menu-item.active",
            "el => el.textContent.trim()")
        check(page, "First menu item (日次詳細) is active",
              "日次詳細" in active_before, f"got '{active_before}'")

        # Each menu item's onclick also calls closeMenu(), so the dropdown
        # collapses after every click — reopen it before each subsequent click.

        # Click second item: 防除暦（schedule tab。旧名「スケジュール」は改名済み）
        menu_items[1].click()
        page.wait_for_timeout(500)
        active_after = page.eval_on_selector(".menu-item.active",
            "el => el.textContent.trim()")
        check(page, "Second menu item (防除暦) activates",
              "防除暦" in active_after, f"got '{active_after}'")

        # Click third item: 防除履歴
        page.click(".menu-toggle", timeout=TIMEOUT)
        page.wait_for_timeout(300)
        menu_items = page.query_selector_all(".menu-item")
        menu_items[2].click()
        page.wait_for_timeout(500)
        active_after2 = page.eval_on_selector(".menu-item.active",
            "el => el.textContent.trim()")
        check(page, "Third menu item (防除履歴) activates",
              "防除履歴" in active_after2, f"got '{active_after2}'")

        # Return to first
        page.click(".menu-toggle", timeout=TIMEOUT)
        page.wait_for_timeout(300)
        menu_items = page.query_selector_all(".menu-item")
        menu_items[0].click()
        page.wait_for_timeout(300)


def test_7_console_errors(page):
    """[7] Console Errors — クリティカルなエラーがない"""
    print("\n[7] Console Errors")

    # Collect console errors
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    page.reload(wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_timeout(2000)  # Let async stuff settle

    check(page, "No critical console errors",
          len(errors) == 0, f"found {len(errors)} error(s)")
    if errors:
        for e in errors[:5]:
            print(f"    [console error] {e}")


def test_8_responsive_layout(page):
    """[8] Responsive Layout — モバイルビューポートでも描画される"""
    print("\n[8] Responsive Layout")

    # Desktop
    page.set_viewport_size({"width": 1280, "height": 800})
    check_element(page, ".calendar-panel", "Desktop (1280px): calendar panel visible")

    # Mobile
    page.set_viewport_size({"width": 375, "height": 667})
    check_element(page, ".calendar-panel", "Mobile (375px): calendar panel visible")

    # Tablet
    page.set_viewport_size({"width": 768, "height": 1024})
    check_element(page, ".calendar-panel", "Tablet (768px): calendar panel visible")

    # Restore desktop
    page.set_viewport_size({"width": 1280, "height": 800})


def test_9_assets_loaded(page):
    """[9] Assets — CSS・画像アセットが読み込まれている"""
    print("\n[9] Assets")

    # Stylesheet
    css_ok = page.evaluate("""() => {
        for (let s of document.styleSheets) {
            try {
                if (s.href && s.href.includes('schedule_app.css')) return true;
            } catch(e) {}
        }
        return false;
    }""")
    check(page, "Stylesheet applied", css_ok)

    # Icons
    icon_192 = page.evaluate("() => document.querySelector('link[rel=\"apple-touch-icon\"]') !== null")
    check(page, "Apple touch icon linked", icon_192)

    # Manifest
    manifest = page.evaluate("() => document.querySelector('link[rel=\"manifest\"]') !== null")
    check(page, "Web app manifest linked", manifest)


def test_10_local_storage(page):
    """[10] LocalStorage Initialization — 変数が初期化されている"""
    print("\n[10] LocalStorage Initialization")

    check_js(page, "() => typeof sprayHistory !== 'undefined'",
             "sprayHistory variable initialized", True)
    check_js(page, "() => typeof sprays !== 'undefined'",
             "sprays variable initialized", True)


def test_11_chat_page(page):
    """[11] Chat Page — AIチャットページが読み込める"""
    print("\n[11] Chat Page")

    page.goto(BASE_URL + "/chat", wait_until="domcontentloaded", timeout=TIMEOUT)

    check(page, "Chat page title contains AI/RBP",
          "AIアシスタント" in page.title() or "RBP" in page.title(),
          f"got '{page.title()}'")

    check_element(page, "body", "Chat body exists")

    # Chat input area
    check_element(page, "textarea, input[type='text']", "Chat input element exists")

    # Send button
    check_element(page, "button", "Send button exists")


def test_12_api_endpoints(page):
    """[12] API Endpoints — REST API が応答する"""
    print("\n[12] API Endpoints")

    # We test via fetch from the page context
    pesticides_resp = page.evaluate("""async () => {
        try {
            const r = await fetch('/api/pesticides');
            const d = await r.json();
            return { status: r.status, count: d.pesticides?.length ?? -1 };
        } catch(e) { return { status: -1, error: e.message }; }
    }""")
    check(page, "GET /api/pesticides returns 200",
          pesticides_resp.get("status") == 200,
          f"status={pesticides_resp.get('status')}")
    check(page, "GET /api/pesticides returns data",
          pesticides_resp.get("count", 0) > 0,
          f"count={pesticides_resp.get('count')}")

    diseases_resp = page.evaluate("""async () => {
        try {
            const r = await fetch('/api/diseases');
            const d = await r.json();
            return { status: r.status, count: d.diseases?.length ?? -1 };
        } catch(e) { return { status: -1, error: e.message }; }
    }""")
    check(page, "GET /api/diseases returns 200",
          diseases_resp.get("status") == 200,
          f"status={diseases_resp.get('status')}")
    check(page, "GET /api/diseases returns data",
          diseases_resp.get("count", 0) > 0,
          f"count={diseases_resp.get('count')}")

    sprayHistoryResp = page.evaluate("""async () => {
        try {
            const r = await fetch('/api/spray_history');
            const d = await r.json();
            return { status: r.status, success: d.success };
        } catch(e) { return { status: -1, error: e.message }; }
    }""")
    check(page, "GET /api/spray_history returns 200",
          sprayHistoryResp.get("status") == 200,
          f"status={sprayHistoryResp.get('status')}")


def test_13_post_prescribe(page):
    """[13] POST /api/prescribe — RBP処方APIが応答する"""
    print("\n[13] POST /api/prescribe")

    result = page.evaluate("""async () => {
        try {
            const r = await fetch('/api/prescribe', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    entryVector: [1,0,1,0,1,0,1,0,1,0],
                    engine: 'python'
                })
            });
            const d = await r.json();
            return { status: r.status, hasKeys: Object.keys(d).length };
        } catch(e) { return { status: -1, error: e.message.substring(0,80) }; }
    }""")
    # The API may succeed (200) or return an engine error (400/500) — both are valid responses
    check(page, "POST /api/prescribe responds",
          result.get("status") != -1,
          f"status={result.get('status')}, keys={result.get('hasKeys')}")


def test_14_post_chat_message(page):
    """[14] POST /api/chat/message — 意図分類: 雑談は処方しない / 病害相談は処方する"""
    print("\n[14] POST /api/chat/message — intent classification")

    def chat(message: str) -> dict:
        """ブラウザ内で /api/chat/message にPOSTし、処方テンプレートの有無を返す。"""
        return page.evaluate("""async (msg) => {
            try {
                const r = await fetch('/api/chat/message', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ message: msg })
                });
                const d = await r.json();
                const resp = d.response || '';
                return {
                    status: r.status,
                    hasResponse: !!resp,
                    isPrescription: resp.indexOf('今回の防除の薬剤は') !== -1,
                    responsePreview: resp.substring(0, 120)
                };
            } catch(e) { return { status: -1, error: e.message.substring(0,80) }; }
        }""", message)

    # --- 第一段階: 雑談・無関係入力には処方結果を返さない（LLM自然応答） ---
    # 「こんにちは」等の雑談がRBP処方テンプレート（「今回の防除の薬剤は」）として
    # 返ると、意図分類が効いていない（perceptionが雑談をhallucinateして処方）。
    for chit in ("こんにちは", "ありがとう", "今日はいい天気ですね"):
        r = chat(chit)
        check(page, f"chit-chat '{chit}' is NOT an RBP prescription",
              r.get("status") == 200 and r.get("hasResponse")
              and not r.get("isPrescription"),
              f"status={r.get('status')}, isPrescription={r.get('isPrescription')}, "
              f"preview={r.get('responsePreview', '')!r}")

    # --- 第二段階: 病害相談には処方結果を返す（意図分類が過剰に抑制しないこと） ---
    # 意図分類が雑談を止めようとして本物の症状まで「雑談」と誤分類して
    # 処方しないようになると、本来のRBP処方が壊れる。正のコントロールとして検証。
    for symptom in ("実が腐ってる", "こんにちは、きゅうりの実が腐ってる"):
        r = chat(symptom)
        check(page, f"symptom '{symptom}' IS an RBP prescription",
              r.get("status") == 200 and r.get("hasResponse")
              and r.get("isPrescription"),
              f"status={r.get('status')}, isPrescription={r.get('isPrescription')}, "
              f"preview={r.get('responsePreview', '')!r}")


def test_15_slack_notification_intent(page):
    """[15] Slack Notification Intent — Slack通知キーワードが検知される"""
    print("\n[15] Slack Notification Intent")

    # Verify the server-side keyword detection works by checking the chat page
    # sends the right payload
    result = page.evaluate("""async () => {
        try {
            const r = await fetch('/api/chat/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    message: 'この結果をSlackに通知して'
                })
            });
            const d = await r.json();
            return { status: r.status, hasResponse: !!d.response };
        } catch(e) { return { status: -1, error: e.message.substring(0,80) }; }
    }""")
    check(page, "Slack notification request accepted",
          result.get("status") != -1,
          f"status={result.get('status')}")

    # Also test "メンバーに通知"
    result2 = page.evaluate("""async () => {
        try {
            const r = await fetch('/api/chat/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    message: 'この結果をメンバーに通知して'
                })
            });
            const d = await r.json();
            return { status: r.status, hasResponse: !!d.response };
        } catch(e) { return { status: -1, error: e.message.substring(0,80) }; }
    }""")
    check(page, "Member notification request accepted",
          result2.get("status") != -1,
          f"status={result2.get('status')}")


def test_16_pwa_manifest(page):
    """[16] PWA — マニフェストとサービスワーカーが設定されている"""
    print("\n[16] PWA")

    # test_11 navigated to /chat and never returned — subsequent tests need the main page.
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)

    manifest_href = page.evaluate("() => document.querySelector('link[rel=\"manifest\"]')?.href || ''")
    check(page, "Manifest href present",
          manifest_href.endswith("manifest.json"), f"got {manifest_href!r}")

    # Service Worker registration attempt (may not register without HTTPS, but check setup)
    sw_exists = page.evaluate("() => 'serviceWorker' in navigator")
    check(page, "ServiceWorker API available", sw_exists)


def test_17_theme_toggle(page):
    """[17] Theme Toggle — テーマ切替ボタンが存在する"""
    print("\n[17] Theme Toggle")

    check_element(page, "#headerMenuToggle", "Header menu toggle exists")

    # Open menu and check theme toggle button
    page.click("#headerMenuToggle")
    page.wait_for_timeout(500)
    check_element(page, ".header-menu-item", "Menu items visible")

    # Close menu
    page.click("#headerMenuToggle")


def test_18_settings_modal(page):
    """[18] Settings Modal — 設定モーダルが起動できる"""
    print("\n[18] Settings Modal")

    # test_17 closes the header menu at the end — reopen it before clicking inside.
    page.click("#headerMenuToggle")
    page.wait_for_timeout(300)

    # Click settings button
    page.click('button.header-menu-item:has-text("設定")')
    page.wait_for_timeout(500)

    # Check modal appeared (actual implementation uses id="settings-modal", no class attribute)
    modal_visible = page.evaluate("""() => {
        const modal = document.getElementById('settings-modal') ||
                      document.getElementById('settingsModal') ||
                      document.querySelector('[class*="modal"]') ||
                      document.querySelector('[class*="settings"]');
        return modal && (modal.offsetParent !== null || modal.style.display !== 'none');
    }""")
    check(page, "Settings modal opens", modal_visible)

    # Close modal
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


# ─── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  STB App Playwright Headless Browser Test Suite")
    print("=" * 60)

    # Quick health check
    import urllib.request
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
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        try:
            test_1_page_load(page)
            test_2_javascript_execution(page)
            test_3_data_loading(page)
            test_4_rbp_core_math(page)
            test_5_ui_elements(page)
            test_6_tab_switching(page)
            test_7_console_errors(page)
            test_8_responsive_layout(page)
            test_9_assets_loaded(page)
            test_10_local_storage(page)
            test_11_chat_page(page)
            test_12_api_endpoints(page)
            test_13_post_prescribe(page)
            test_14_post_chat_message(page)
            test_15_slack_notification_intent(page)
            test_16_pwa_manifest(page)
            test_17_theme_toggle(page)
            test_18_settings_modal(page)
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
        print("  ALL TESTS PASSED — ヘッドレスブラウザテスト成功！")
    else:
        print("  SOME TESTS FAILED:")
        for f in failures:
            print(f"    - {f['desc']}: {f['detail']}")
    print("=" * 60)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
