"""責任の帰還（C層・§2.10・§11.4）検証 — 確認イベントを役割保持者へ record_return。

真界_事業主体設計.md §2.10「役割は『要求→確認』の単一の糸」:
  役割 X が「要求を発生 → 責務（期限＋期待状態）を負う → 確認で責任を負う」。
  責任は必ず**人（役割保持者）**で終わる（§2.7: 権限と責務は委任できるが責任は
  委任できない）。

C層（server.py・責任を役割保持者に・挙動変化）:
  確認イベント（検鏡判定確定・定植完了・散布完了）を、その業務の
  responsible_role_id の保持者（worker）へ解決し、record_return で状態記録する。
  responsible_role_id が NULL なら後方互換として subject の事業主（roles.name=
  '事業主'）に解決。

検証項目:
  C1  役割解決 — responsible_role_id=NULL（散布）→ 事業主（roles.id）
  C2  役割解決 — responsible_role_id 指定（定植）→ その役割
  C3  帰還 — 散布完了（NULL=事業主）→ 事業主保持者 worker へ state 記録
  C4  帰還 — 定植完了（防除課長）→ 防除課長保持者 worker へ state 記録
  C5  帰還 — 保持者なし（holds 未設定）→ None（記録しない・更新を阻害しない）

※ 一時DB で実行。本番 data/stb.db は触らない。humos.record_return は
  humos._DB_PATH の接続に書くため、fixture が一時DB に差し替える。
"""

import os
import sqlite3
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

import humos  # noqa: E402
import server  # noqa: E402
from humos_os import schema, entity, plan  # noqa: E402

SUBJECT = "mikakura"   # 事業主体（三果倉）＝ net_id
DOMAIN = "iichigo"     # 事業ドメイン（いちご）


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """humos._conn() を一時DB に差し替え、STB ローカル roles テーブルも作る。"""
    tmp = str(tmp_path / "accountability_test.db")
    monkeypatch.setattr(humos, "_DB_PATH", tmp)
    humos._conn_local.conn = None
    c = humos._conn()  # スキーマ＋NET place を保証
    c.row_factory = sqlite3.Row
    schema.ensure_schema(c)
    entity.ensure_entity_schema(c)
    plan.ensure_plan_schema(c)
    # STB ローカルの roles テーブル（server.py init_db と同一形状）。
    c.execute("""CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        subject_id TEXT NOT NULL DEFAULT 'mikakura',
        notes TEXT, created_at TEXT, updated_at TEXT)""")
    yield c
    humos._conn_local.conn = None
    c.close()
    if os.path.exists(tmp):
        os.unlink(tmp)


def _seed(conn):
    """worker 群・役割・保持者・schedule_def（責務）を置く。

        worker/6（松山浩士）・worker/7（志保）
        role/1（事業主）─holds→ worker/6
        role/3（防除課長）─holds→ worker/7
        role/9（未就任）─holds なし（C5 用）
        schedule_def: 定植 responsible_role_id='3'（防除課長）・防除 NULL（事業主）
    """
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "worker", "6", "松山浩士")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "worker", "7", "志保")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "1", "事業主")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "3", "防除課長")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "9", "未就任")
    entity.assign_role(conn, SUBJECT, DOMAIN, "1", "6")
    entity.assign_role(conn, SUBJECT, DOMAIN, "3", "7")
    # STB ローカル roles テーブル（id が entity の role object_id と一致）。
    conn.execute("INSERT INTO roles (id, name) VALUES (1, '事業主')")
    conn.execute("INSERT INTO roles (id, name) VALUES (3, '防除課長')")
    conn.execute("INSERT INTO roles (id, name) VALUES (9, '未就任')")
    # 責務（PLAN）: 定植=防除課長・防除=NULL（後方互換=事業主）。
    plan.add_schedule_def(conn, def_id="d1", net_id=SUBJECT, name="定植",
                          target_key="subject", target_value="本圃",
                          expected_state="P_transplant", cadence="oneshot",
                          deadline_spec={"at": "2026-10-08T08:00"},
                          responsible_role_id="3")
    plan.add_schedule_def(conn, def_id="d2", net_id=SUBJECT, name="防除",
                          target_key="subject", target_value="育苗圃",
                          expected_state="P_rx_exec", cadence="oneshot",
                          deadline_spec={"at": "2026-09-20T08:00"})
    conn.commit()


def _state_attr(conn, worker_id, key):
    r = conn.execute(
        "SELECT attr_value FROM entity_attribute WHERE subject_id=? AND domain_id=? "
        "AND entity_id='worker' AND object_id=? AND attr_key=? AND attr_kind='state'",
        (SUBJECT, DOMAIN, worker_id, key)).fetchone()
    return r["attr_value"] if r else None


def test_c1_resolve_null_falls_back_to_shujugyoshu(conn):
    """C1: responsible_role_id=NULL（散布→防除）→ 事業主（roles.id=1）。"""
    _seed(conn)
    rid, rname = server._responsible_role_id_for(conn, "spray")
    assert rid == "1"
    assert rname == "事業主"


def test_c2_resolve_specified_role(conn):
    """C2: responsible_role_id 指定（定植→定植 def）→ その役割（防除課長=3）。"""
    _seed(conn)
    rid, rname = server._responsible_role_id_for(conn, "transplant")
    assert rid == "3"
    assert rname == "防除課長"
    # 検鏡も定植コホートの責務（同一 group_id）→ 防除課長。
    assert server._responsible_role_id_for(conn, "inspection") == ("3", "防除課長")


def test_c3_return_spray_to_shujugyoshu_holder(conn):
    """C3: 散布完了（NULL=事業主）→ 事業主保持者 worker/6 へ state 記録。"""
    _seed(conn)
    res = server._record_accountability(conn, "spray", 101, "散布完了")
    assert res == {"role": "事業主", "holder": "松山浩士", "worker_object_id": "6"}
    assert _state_attr(conn, "6", "accountability_spray_101") is not None


def test_c4_return_transplant_to_boujokuchou_holder(conn):
    """C4: 定植完了（防除課長）→ 防除課長保持者 worker/7（志保）へ state 記録。"""
    _seed(conn)
    res = server._record_accountability(conn, "transplant", 202, "定植完了")
    assert res == {"role": "防除課長", "holder": "志保", "worker_object_id": "7"}
    assert _state_attr(conn, "7", "accountability_transplant_202") is not None
    # 事業主保持者（worker/6）には落ちない（役割が異なると保持者が変わる）。
    assert _state_attr(conn, "6", "accountability_transplant_202") is None


def test_c5_no_holder_returns_none(conn):
    """C5: 保持者なし（holds 未設定）→ None（記録しない・更新を阻害しない）。"""
    _seed(conn)
    # 未就任（role/9）に保持者を設定せず、定植の責務を未就任へ向ける。
    conn.execute("UPDATE schedule_def SET responsible_role_id='9' WHERE def_id='d1'")
    conn.commit()
    res = server._record_accountability(conn, "transplant", 303, "定植完了")
    assert res is None
    assert _state_attr(conn, "6", "accountability_transplant_303") is None
    assert _state_attr(conn, "7", "accountability_transplant_303") is None
