"""記号化事実（能力事実）のデータ基盤（フェーズ A）検証。

IF 設計（[ts/真界_IF設計.md](../ts/真界_IF設計.md) §8.9・§8.10）:
  チャネル解決（②）の根拠は**記号化された事実（データ）**。
  人は「ルーティングのルール」ではなく「事実の記号化」をする。

検証項目:
  A1  能力事実（attr_kind=state）の set/get/has/list
  A2  attr_kind=requirement の属性は能力事実として読まない（境界）
  A3  冪等 upsert（同一 key で上書き）
  D   チャネル解決（resolve_channel）: 能力事実からアダプタを選ぶ
      - 導入済み → AppAPI（記符号的実働）
      - 未導入/不一致 → Slack（物理的実働・フォールバック）

4-level ID: subject_id="mikakura" ⊃ domain_id="iichigo" ⊃ entity_id="field" ⊃ object_id。

※ 一時DB で実行。本番 data/stb.db は触らない。
"""

import os
import sqlite3
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from humos_os import entity  # noqa: E402
from sos.adapter import SlackAdapter  # noqa: E402
import capability  # noqa: E402

SUBJECT = "mikakura"   # 事業主体（三果倉）
DOMAIN = "iichigo"     # 事業ドメイン（いちご）
TARGET = {"subject_id": SUBJECT, "domain_id": DOMAIN,
          "entity_id": "field", "object_id": "2"}


@pytest.fixture
def db(tmp_path):
    """一時DBに entity スキーマ＋圃場 entity（field "2"=本圃）を投入する。"""
    tmp = str(tmp_path / "cap_test.db")
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row
    entity.ensure_entity_schema(conn)
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "2", "本圃")
    yield conn
    conn.close()
    if os.path.exists(tmp):
        os.unlink(tmp)


# ============================================================================
# A1  能力事実の set/get/has/list
# ============================================================================

def test_set_get_capability(db):
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "deployed")
    assert capability.get_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app") == "deployed"


def test_get_capability_none_when_absent(db):
    assert capability.get_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app") is None


def test_has_capability(db):
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "deployed")
    assert capability.has_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "deployed")
    assert not capability.has_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "not_deployed")


def test_list_capabilities(db):
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "deployed")
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "irrigation_app", "deployed")
    caps = capability.list_capabilities(db, SUBJECT, DOMAIN, "field", "2")
    assert caps == {"inventory_app": "deployed", "irrigation_app": "deployed"}


# ============================================================================
# A2  attr_kind=requirement は能力事実として読まない（境界）
# ============================================================================

def test_get_capability_ignores_requirement_kind(db):
    """attr_kind=requirement の属性は能力事実（state）として読まない。"""
    entity.set_attribute(db, SUBJECT, DOMAIN, "field", "2", "spray_volume", "50.0", attr_kind="requirement")
    assert capability.get_capability(db, SUBJECT, DOMAIN, "field", "2", "spray_volume") is None


# ============================================================================
# A3  冪等 upsert
# ============================================================================

def test_set_capability_idempotent(db):
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "deployed")
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "not_deployed")
    assert capability.get_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app") == "not_deployed"


# ============================================================================
# D  チャネル解決（resolve_channel）: 能力事実からアダプタを選ぶ
# ============================================================================

def _make_adapter(name, serves, priority, requires):
    class A(SlackAdapter):
        pass
    A.name = name
    A.serves = frozenset(serves)
    A.priority = priority
    A.requires = requires
    A.send = lambda self, req: {"success": True, "via": name}
    return A()


def test_resolve_channel_by_capability(db):
    """能力事実（導入済み）で AppAPI が解決される（記符号的実働）。"""
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "deployed")
    app = _make_adapter("app_api", {"inventory_check"}, 10, {"inventory_app": "deployed"})
    slack = SlackAdapter()
    adapter = capability.resolve_channel(
        db, {"type": "inventory_check"}, TARGET, [slack, app])
    assert adapter.name == "app_api"


def test_resolve_channel_falls_back_when_not_deployed(db):
    """能力事実なし（未導入）で Slack にフォールバック（物理的実働）。"""
    app = _make_adapter("app_api", {"inventory_check"}, 10, {"inventory_app": "deployed"})
    slack = SlackAdapter()
    adapter = capability.resolve_channel(
        db, {"type": "inventory_check"}, TARGET, [slack, app])
    assert adapter.name == "slack"


def test_resolve_channel_requires_mismatch(db):
    """requires の値が一致しない（not_deployed）で Slack にフォールバック。"""
    capability.set_capability(db, SUBJECT, DOMAIN, "field", "2", "inventory_app", "not_deployed")
    app = _make_adapter("app_api", {"inventory_check"}, 10, {"inventory_app": "deployed"})
    slack = SlackAdapter()
    adapter = capability.resolve_channel(
        db, {"type": "inventory_check"}, TARGET, [slack, app])
    assert adapter.name == "slack"


def test_resolve_channel_no_adapters_raises(db):
    """アダプタが 0 件なら RuntimeError（閉包違反の検知）。"""
    with pytest.raises(RuntimeError):
        capability.resolve_channel(db, {"type": "x"},
                                   {"subject_id": SUBJECT, "domain_id": DOMAIN}, [])
