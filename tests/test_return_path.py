"""帰還経路（§2.4）検証 — 実働完了 → 記録 → ドメインエンティティオブジェクト。

IF 設計（[ts/真界_IF設計.md](../ts/真界_IF設計.md) §2.4）:
  物理界の実働（人・機械・アプリ）が**終われば**、その**実行記録・状態記録**が
  HUMOS のドメインエンティティオブジェクトに戻る。系はここでドメインレベルで閉じる。

帰還は2段:
  ① 状態記録（本体）: entity.set_attribute(attr_kind="state") でドメインエンティティ
     オブジェクトの現在状態を更新（SSS の認知対象・次の要求のトリガー）。
  ② STN 再開（帰結）: place_external_token（マーキング変化）→ resume_net（持続させる
     主体）で後続 T を発火させる。§2.2 の在庫帰還は②のみの特殊ケース。

検証項目:
  R1  ①状態記録: record_return(token_key=None) → entity_attribute に state が落ち、
      capability.get_capability が読める（記号化事実として帰還）。
  R2  ①+②配線: token_key を渡すと place_external_token がマーキングに置き、
      resume_net（持続させる主体）が呼ばれる（本番ネットは monkeypatch で回避）。
  R3  記録主体: source が entity_attribute に記録される（人/機械を問わない）。

※ 一時DB で実行。本番 data/stb.db は触らない。
"""

import os
import sqlite3
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

import humos  # noqa: E402
from humos_os import schema, entity  # noqa: E402
import capability  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """humos._conn() を一時DB に差し替える（本番 data/stb.db を触らない）。"""
    tmp = str(tmp_path / "return_test.db")
    monkeypatch.setattr(humos, "_DB_PATH", tmp)
    # 遅延接続をリセット（スレッドローカル）。
    humos._conn_local.conn = None
    conn = humos._conn()  # スキーマ＋NET place を保証
    entity.ensure_entity_schema(conn)  # entity / entity_attribute / object_component
    yield conn
    humos._conn_local.conn = None
    conn.close()
    if os.path.exists(tmp):
        os.unlink(tmp)


def test_return_state_record_to_entity(db):
    """R1: ①状態記録 — record_return(token_key=None) → entity_attribute に state。"""
    res = humos.record_return(
        "mikakura", "iichigo", "field", "2",
        attr_key="spray_completed", attr_value="true",
        token_key=None, source="machine")
    assert res["state"] is True
    assert res["resumed"] is None  # ②なし（token_key=None）

    # 記号化事実として帰還（capability が attr_kind=state で読む）
    assert capability.get_capability(db, "mikakura", "iichigo", "field", "2", "spray_completed") == "true"
    assert capability.has_capability(db, "mikakura", "iichigo", "field", "2", "spray_completed", "true")


def test_return_wires_stn_resume(db, monkeypatch):
    """R2: ①+②配線 — token_key があると place_external_token→resume_net。"""
    # 本番ネット（RBP エンジン）を走らせない: resume_net を記録用の no-op に差し替え。
    calls = []
    import jobnet
    monkeypatch.setattr(jobnet, "resume_net", lambda: calls.append("resumed"))

    res = humos.record_return(
        "mikakura", "iichigo", "field", "2",
        attr_key="inventory_checked", attr_value="ok",
        token_key="inventory_token", token_payload={"checked": True},
        source="physical_operator")
    assert res["state"] is True
    assert calls == ["resumed"]  # ②持続させる主体が呼ばれた

    # ①状態記録も落ちている
    assert capability.get_capability(db, "mikakura", "iichigo", "field", "2", "inventory_checked") == "ok"
    # ②マーキングに外部トークンが置かれた（IF 感覚ポート）
    marking = humos.get_marking()
    assert marking.get("inventory_token") == {"checked": True}


def test_return_source_recorded(db):
    """R3: 記録主体（source）が entity_attribute に記録される。"""
    humos.record_return("mikakura", "iichigo", "field", "2",
                        attr_key="patrol_done", attr_value="yes",
                        token_key=None, source="physical_operator")
    attrs = entity.get_attributes(db, "mikakura", "iichigo", "field", "2")
    row = attrs.get("patrol_done")
    assert row is not None
    assert row["attr_kind"] == "state"
    assert row["source"] == "physical_operator"
