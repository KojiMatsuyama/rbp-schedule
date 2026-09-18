"""入方向 IF（石5）検証 — ドメインオブジェクトが要求を STN に届ける。

IF 設計（[ts/真界_IF設計.md](../ts/真界_IF設計.md) §2.2・§8.8）:
  入方向: 本圃（ドメインオブジェクト）──スケジュール──▶ 要求(作動)──IF──▶ STN（起動）

核心:
  - **STN側のAPI** = `humos.place_external_token`（IF の接続点）。
  - **持続させる主体（監査G3）** = `stn.resume_net`（clear しない発火ループ）。
    run_net（同期バッチ）は clear_transient で外部トークンを消すため、
    入方向・非同期再開は resume_net で発火させる。

検証項目:
  I1  入方向: place_external_token → resume_net → T 発火（外部トークンが T を起動）
  I2  外部トークンなし → resume_net は発火しない（fixpoint）
  I3  再発火防止: 2回目の resume_net は既に発火した T を再発火しない

※ 一時DB で実行。本番 data/stb.db は触らない。
"""

import os
import sqlite3
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from humos_os import schema, stn, humos as _h  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """一時DBに 5 テーブル（place/token/marking/firing/writing）を投入する。"""
    tmp = str(tmp_path / "inbound_test.db")
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row
    schema.ensure_schema(conn)
    yield conn
    conn.close()
    if os.path.exists(tmp):
        os.unlink(tmp)


# 合成ネット: 要求トークンが T_req を起動し、done_token を生成。
PLACES = {
    "P_req": {"name": "要求", "token_key": "requirement_token", "store": "humos"},
    "P_done": {"name": "完了", "token_key": "done_token", "store": "humos"},
}
TRANSITIONS = {
    "T_req": {
        "name": "要求処理",
        "handler": "synthetic",
        "pre": ["P_req"],
        "post": ["P_done"],
        "enabled_when": ["requirement_token"],
    },
}


def _resolve(name):
    """合成ハンドラの解決（resume_net の resolve_handler 引数）。"""
    def handler(state):
        return {"done_token": "done"}
    return handler


def _register_places(db):
    """本番の前提を再現: place は接続時に事前登録される（_ensure_net_places）。

    place_external_token は _place_marking 経由で token_key→place を解決するため、
    外部トークンを置く前に place が place テーブルに存在する必要がある。
    """
    _h.ensure_places(db, {
        pid: {
            "name": p.get("name", pid),
            "kind": p.get("kind", "state"),
            "capacity": p.get("capacity"),
            "token_key": p.get("token_key"),
        }
        for pid, p in PLACES.items()
    })


def _fired(db, net):
    """トランジション発火（E: 外部イベント行を除外）のリスト。"""
    return [r["transition_id"] for r in db.execute(
        "SELECT transition_id FROM firing WHERE net_id=? "
        "AND transition_id NOT LIKE 'E:%'", (net,)).fetchall()]


def test_inbound_external_token_fires_transition(db):
    """I1: place_external_token → resume_net → T 発火（入方向）。"""
    net = "inbound_1"
    _register_places(db)
    _h.place_external_token(
        db, net, "requirement_token",
        {"type": "spray", "target": {"subject_id": "mikakura", "domain_id": "iichigo", "entity_id": "field", "object_id": "2"}},
        event={"source": "domain_object"})

    # マーキングに置かれた（IF 接続点）
    assert _h.get_marking_payloads(db, net).get("requirement_token") is not None

    # 持続させる主体（resume_net）が発火させる
    stn.resume_net(db, net, PLACES, TRANSITIONS, _resolve, hard_consume=False)

    # T_req が発火し、done_token が生成された
    assert "T_req" in _fired(db, net)
    assert _h.get_marking_payloads(db, net).get("done_token") == "done"


def test_inbound_no_token_no_fire(db):
    """I2: 外部トークンなし → resume_net は発火しない（fixpoint）。"""
    net = "inbound_2"
    stn.resume_net(db, net, PLACES, TRANSITIONS, _resolve, hard_consume=False)
    assert "T_req" not in _fired(db, net)


def test_inbound_no_refire(db):
    """I3: 再発火防止 — 2回目の resume_net は T_req を再発火しない。"""
    net = "inbound_3"
    _register_places(db)
    _h.place_external_token(db, net, "requirement_token", {"type": "spray"})
    stn.resume_net(db, net, PLACES, TRANSITIONS, _resolve, hard_consume=False)
    # 2回目（fired は firing ログから復元 → T_req は再発火しない）
    stn.resume_net(db, net, PLACES, TRANSITIONS, _resolve, hard_consume=False)
    count = db.execute(
        "SELECT COUNT(*) FROM firing WHERE net_id=? AND transition_id='T_req'",
        (net,)).fetchone()[0]
    assert count == 1
