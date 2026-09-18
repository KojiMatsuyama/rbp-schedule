"""IF（膜）ファサード（①挙動不変リファクタ）検証。

IF 設計（[ts/真界_IF設計.md](../ts/真界_IF設計.md) §8）:
  投射（内容生成、T 内部）→ 作動（起動）──IF──▶ 実働（実行）

①（挙動不変リファクタ）:
  - sos.membrane.deliver が現行の Slack 経路（sos.slack.send_message）に委譲する
  - write_sos（作動ログ）は 1 回だけ（membrane が二重記録しない）
  - str / dict 両方の requirement が通る

②（自動チャネル解決）への継ぎ目:
  - _resolve が serves ∩ available_at のマッチング＋優先度で解決する
  - 高優先度アダプタを登録すると解決が変わる（記号化事実が揃うと②が動く）

※ 本番DB・Slack Webhook は触らない（chat_client / humos.write_sos をモック）。
"""

import os
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

import chat_client  # noqa: E402
import humos  # noqa: E402
import sos  # noqa: E402
from sos import membrane  # noqa: E402
from sos.adapter import SlackAdapter  # noqa: E402


# ============================================================================
# ① 挙動不変: deliver が Slack 経路に委譲する
# ============================================================================

def test_deliver_str_routes_to_slack(monkeypatch):
    """deliver(str) が SlackAdapter → sos.slack.send_message に委譲する（挙動不変）。"""
    captured = {}

    def fake_send(text, blocks=None):
        captured["text"] = text
        return {"success": True}

    monkeypatch.setattr(chat_client, "send_message", fake_send)
    # write_sos は 1 回だけ呼ばれることを検証するためカウント（モック）
    calls = []
    monkeypatch.setattr(humos, "write_sos", lambda action, result: calls.append(action))

    result = sos.membrane.deliver("散布処方: 炭疽病 → X剤")

    assert result == {"success": True}
    assert captured["text"] == "散布処方: 炭疽病 → X剤"
    # 作動ログは 1 回だけ（membrane が二重記録しない）
    assert calls == ["slack_send"]


def test_deliver_dict_with_type_target(monkeypatch):
    """deliver(dict) が type/target を保持して Slack 経路に委譲する。"""
    captured = {}

    def fake_send(text, blocks=None):
        captured["text"] = text
        return {"success": True}

    monkeypatch.setattr(chat_client, "send_message", fake_send)
    monkeypatch.setattr(humos, "write_sos", lambda action, result: None)

    req = {
        "type": "inventory_check",
        "target": {"subject_id": "mikakura", "domain_id": "iichigo",
                   "entity_id": "field", "object_id": "1"},
        "text": "在庫確認: 圃場A",
    }
    result = sos.membrane.deliver(req)

    assert result == {"success": True}
    assert captured["text"] == "在庫確認: 圃場A"


def test_deliver_card_routes_to_send_card(monkeypatch):
    """deliver(dict with card) が sos.slack.send_card に委譲する。"""
    captured = {}

    def fake_card(title, sections, footer=None):
        captured["title"] = title
        return {"success": True}

    monkeypatch.setattr(chat_client, "send_message_with_card", fake_card)
    monkeypatch.setattr(humos, "write_sos", lambda action, result: None)

    result = sos.membrane.deliver({
        "card": {"title": "散布通知", "sections": [{"header": "h", "fields": []}]},
    })

    assert result == {"success": True}
    assert captured["title"] == "散布通知"


# ============================================================================
# ① 解決: 現行は Slack のみ
# ============================================================================

def test_resolve_returns_slack_when_only_slack():
    """チャネルが Slack のみなら _resolve は常に SlackAdapter を返す。"""
    adapter = membrane._resolve({"type": "inventory_check"},
                                {"subject_id": "mikakura", "domain_id": "iichigo"})
    assert isinstance(adapter, SlackAdapter)
    assert adapter.name == "slack"


# ============================================================================
# ② への継ぎ目: アダプタ登録で解決が変わる
# ============================================================================

def test_resolve_picks_higher_priority_adapter(monkeypatch):
    """高優先度アダプタを登録すると _resolve がそれを返す（②の継ぎ目）。"""

    class FakeAppAdapter(SlackAdapter):
        name = "app_api"
        priority = 10   # Slack(0) より優先（記符号的実働 > 物理的実働）
        serves = frozenset({"inventory_check"})

        def send(self, requirement):
            return {"success": True, "via": "app_api"}

    fake = FakeAppAdapter()
    orig = list(membrane._ADAPTERS)
    monkeypatch.setattr(membrane, "_ADAPTERS", orig + [fake])

    # 対応する要求種 → 高優先度の app_api に解決
    adapter = membrane._resolve({"type": "inventory_check"},
                                {"subject_id": "mikakura", "domain_id": "iichigo"})
    assert adapter.name == "app_api"

    # 非対応の要求種 → serves にマッチしないので Slack にフォールバック
    adapter2 = membrane._resolve({"type": "other"},
                                 {"subject_id": "mikakura", "domain_id": "iichigo"})
    assert adapter2.name == "slack"


def test_resolve_falls_back_when_no_candidate(monkeypatch):
    """available_at が全 False ならフォールバック（先頭=Slack）に解決する。"""

    class UnavailableAdapter(SlackAdapter):
        name = "unavailable"

        def available_at(self, target):
            return False

    orig = list(membrane._ADAPTERS)
    monkeypatch.setattr(membrane, "_ADAPTERS", orig + [UnavailableAdapter()])

    # 登録済みの Slack は available_at=True なので候補にあり、解決される
    adapter = membrane._resolve({"type": "x"}, None)
    assert adapter.name == "slack"


def test_resolve_raises_when_no_adapters(monkeypatch):
    """アダプタが0件なら RuntimeError（閉包違反の検知）。"""
    monkeypatch.setattr(membrane, "_ADAPTERS", [])
    with pytest.raises(RuntimeError):
        membrane._resolve({"type": "x"}, None)


# ============================================================================
# ② 自動チャネル解決（C/D 統合）: conn を渡すと能力事実（記号化事実）から解決
# ============================================================================

def _app_adapter():
    """能力事実を自己記述するアプリアダプタ（記符号的実働・requires 宣言）。"""
    class AppAdapter(SlackAdapter):
        name = "app_api"
        priority = 10
        serves = frozenset({"inventory_check"})
        requires = {"inventory_app": "deployed"}

        def send(self, requirement):
            return {"success": True, "via": "app_api"}
    return AppAdapter()


def test_resolve_with_conn_uses_capability_fact(tmp_path, monkeypatch):
    """conn 渡し → capability.resolve_channel が能力事実から解決（導入済み→AppAPI）。"""
    import sqlite3
    from humos_os import entity
    import capability

    conn = sqlite3.connect(str(tmp_path / "m.db"))
    conn.row_factory = sqlite3.Row
    entity.ensure_entity_schema(conn)
    entity.upsert_entity(conn, "mikakura", "iichigo", "field", "2", "本圃")
    capability.set_capability(conn, "mikakura", "iichigo", "field", "2", "inventory_app", "deployed")

    app = _app_adapter()
    orig = list(membrane._ADAPTERS)
    monkeypatch.setattr(membrane, "_ADAPTERS", orig + [app])

    target = {"subject_id": "mikakura", "domain_id": "iichigo",
              "entity_id": "field", "object_id": "2"}
    # 導入済み → 記符号的実働（app_api）に解決
    adapter = membrane._resolve({"type": "inventory_check"}, target, conn)
    assert adapter.name == "app_api"

    # 未導入圃場（field "3"）→ Slack にフォールバック
    entity.upsert_entity(conn, "mikakura", "iichigo", "field", "3", "本圃2")
    adapter2 = membrane._resolve(
        {"type": "inventory_check"},
        {"subject_id": "mikakura", "domain_id": "iichigo",
         "entity_id": "field", "object_id": "3"}, conn)
    assert adapter2.name == "slack"
    conn.close()


def test_deliver_with_conn_routes_by_capability(tmp_path, monkeypatch):
    """deliver(conn=...) が能力事実でチャネルを選び、そのアダプタの send を呼ぶ。"""
    import sqlite3
    from humos_os import entity
    import capability

    conn = sqlite3.connect(str(tmp_path / "m2.db"))
    conn.row_factory = sqlite3.Row
    entity.ensure_entity_schema(conn)
    entity.upsert_entity(conn, "mikakura", "iichigo", "field", "2", "本圃")
    capability.set_capability(conn, "mikakura", "iichigo", "field", "2", "inventory_app", "deployed")

    app = _app_adapter()
    orig = list(membrane._ADAPTERS)
    monkeypatch.setattr(membrane, "_ADAPTERS", orig + [app])

    result = membrane.deliver(
        {"type": "inventory_check", "text": "在庫確認"},
        {"subject_id": "mikakura", "domain_id": "iichigo",
         "entity_id": "field", "object_id": "2"}, conn)
    assert result == {"success": True, "via": "app_api"}
    conn.close()


def test_deliver_without_conn_unchanged(monkeypatch):
    """conn なしは現行挙動（Slack 経路・DB 不要）を維持（挙動不変）。"""
    captured = {}

    def fake_send(text, blocks=None):
        captured["text"] = text
        return {"success": True}

    monkeypatch.setattr(chat_client, "send_message", fake_send)
    monkeypatch.setattr(humos, "write_sos", lambda action, result: None)

    result = membrane.deliver("散布処方: 炭疽病 → X剤")
    assert result == {"success": True}
    assert captured["text"] == "散布処方: 炭疽病 → X剤"
