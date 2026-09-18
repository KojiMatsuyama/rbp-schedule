"""役割管理（位置的角色・entity_id='role'）のテスト。

真界_事業主体設計.md §2.6.1: 役割（事業主等）は「位置（ポスト）」そのもので、
第一級エンティティ（entity_id='role'）として建模する。要求の発生源は役割
（人物でなく）。3ノード鎖:

    subject/{id} ──[has_role]──▶ role/{id} ──[holds]──▶ worker/{id}

世代交代 = holds エッジの差し替えのみ（役割ノードは不変）。

検証:
  R1  entity.role_holder — 役割の保持者解決
  R2  entity.assign_role — 保持者設定・世代交代（1役割＝1保持者・stale なし）
  R3  entity.list_roles — 主体の役割と保持者一覧
  R4  server._role_context — 役割（事業主）の解決（roles テーブル＋delegation）

venv の python で実行する（humos_os は pip パッケージ・v1.5.0）:
    .venv/bin/python -m pytest tests/test_role.py -v
"""

import os
import sqlite3
import sys

import pytest

from humos_os import schema, entity

SUBJECT = "mikakura"   # 事業主体（三果倉）＝ net_id
DOMAIN = "iichigo"     # 事業ドメイン（いちご）

# server を import するためのパス（tests/ から 1 階層上）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    schema.ensure_schema(c)        # humos 5 テーブル
    entity.ensure_entity_schema(c)  # entity + entity_attribute + object_component + delegation
    yield c
    c.close()


def _seed_people(conn):
    """主体・役割・従業者（松山浩士=6 / 松山凌梧=8）を entity に置く。"""
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "subject", "1", "三果倉")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "1", "事業主")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "worker", "6", "松山浩士")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "worker", "8", "松山凌梧")
    # 主体→役割（has_role）
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "subject", "1", "has_role",
                           SUBJECT, DOMAIN, "role", "1")


def test_r1_role_holder(conn):
    _seed_people(conn)
    # 未設定なら None
    assert entity.role_holder(conn, SUBJECT, DOMAIN, "1") is None
    # 保持者設定
    entity.assign_role(conn, SUBJECT, DOMAIN, "1", "6")
    h = entity.role_holder(conn, SUBJECT, DOMAIN, "1")
    assert h is not None and h["name"] == "松山浩士" and h["object_id"] == "6"


def test_r2_assign_role_succession(conn):
    _seed_people(conn)
    # 初期: 松山浩士(6)
    entity.assign_role(conn, SUBJECT, DOMAIN, "1", "6")
    assert entity.role_holder(conn, SUBJECT, DOMAIN, "1")["name"] == "松山浩士"
    # 世代交代: 松山凌梧(8)
    entity.assign_role(conn, SUBJECT, DOMAIN, "1", "8")
    assert entity.role_holder(conn, SUBJECT, DOMAIN, "1")["name"] == "松山凌梧"
    # 1役割＝1保持者: holds エッジはちょうど1本（stale な holds→worker/6 が残らない）
    n = conn.execute(
        "SELECT COUNT(*) FROM delegation WHERE entity_id='role' AND object_id='1' "
        "AND rel='holds' AND to_entity_id='worker'").fetchone()[0]
    assert n == 1
    # 役割ノードは不変（世代交代で role entity が消えない）
    assert entity.get_entity(conn, SUBJECT, DOMAIN, "role", "1") is not None


def test_r3_list_roles(conn):
    _seed_people(conn)
    entity.assign_role(conn, SUBJECT, DOMAIN, "1", "6")
    roles = entity.list_roles(conn, SUBJECT, DOMAIN)
    assert len(roles) == 1
    assert roles[0]["role"]["name"] == "事業主"
    assert roles[0]["holder"]["name"] == "松山浩士"
    # 世代交代で一覧の保持者も変わる
    entity.assign_role(conn, SUBJECT, DOMAIN, "1", "8")
    assert entity.list_roles(conn, SUBJECT, DOMAIN)[0]["holder"]["name"] == "松山凌梧"


def test_r4_role_context(tmp_path):
    """server._role_context — roles テーブル＋delegation から役割と保持者を解決。"""
    import server

    db = tmp_path / "role_test.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    schema.ensure_schema(c)
    entity.ensure_entity_schema(c)
    # ドメインDB の roles テーブル（役割ノードの name/同一性の正）
    c.execute(
        "CREATE TABLE roles (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL UNIQUE, subject_id TEXT NOT NULL DEFAULT 'mikakura', "
        "notes TEXT, created_at TEXT, updated_at TEXT)")
    c.execute("INSERT INTO roles (name, subject_id) VALUES ('事業主', 'mikakura')")
    c.commit()
    # entity: 主体・役割・従業者 ＋ 紐付け
    entity.upsert_entity(c, SUBJECT, DOMAIN, "subject", "1", "三果倉")
    entity.upsert_entity(c, SUBJECT, DOMAIN, "role", "1", "事業主")
    entity.upsert_entity(c, SUBJECT, DOMAIN, "worker", "6", "松山浩士")
    entity.sync_delegation(c, SUBJECT, DOMAIN, "subject", "1", "has_role",
                           SUBJECT, DOMAIN, "role", "1")
    entity.assign_role(c, SUBJECT, DOMAIN, "1", "6")
    c.commit()

    rc = server._role_context(c)
    assert rc["role"] == "事業主"
    assert rc["role_id"] == 1
    assert rc["role_holder"] == "松山浩士"

    # 世代交代で _role_context の保持者も変わる
    entity.upsert_entity(c, SUBJECT, DOMAIN, "worker", "8", "松山凌梧")
    entity.assign_role(c, SUBJECT, DOMAIN, "1", "8")
    c.commit()
    assert server._role_context(c)["role_holder"] == "松山凌梧"
    c.close()
