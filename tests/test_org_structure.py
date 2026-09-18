"""複雑組織構造・役割の責務/責任（§2.6.2・§2.10）のテスト。

真界_事業主体設計.md §2.6.2（複雑組織のエンティティ化）と §2.10（役割は
「要求→確認」の単一の糸）の検証。

Layer A（entity.py・組織構造）:
  A1  entity.list_roles — 任意ノード（group/project）から役割を辿る
  A2  entity.roles_held_by — 一人→N役割（二重/三重割り当て）
  A3  entity.org_tree — contains の木（組織階層）
  A4  entity.org_members — member_of（所属・横断）と lead（組織長）

Layer B（plan.py・責務を役割に）:
  B1  plan.ensure_plan_schema — responsible_role_id 列の冪等移行
  B2  plan.add_schedule_def — responsible_role_id の登録
  B3  plan.expected_state_at — responsible_role_id の導出
  B4  コホート（group_id 行群）の責務 = 同一 responsible_role_id

venv の python で実行する（humos_os は pip パッケージ・v1.6.0）:
    .venv/bin/python -m pytest tests/test_org_structure.py -v
"""

import os
import sqlite3
import sys

import pytest

from humos_os import schema, entity, plan

SUBJECT = "mikakura"   # 事業主体（三果倉）＝ net_id
DOMAIN = "iichigo"     # 事業ドメイン（いちご）

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    schema.ensure_schema(c)
    entity.ensure_entity_schema(c)
    plan.ensure_plan_schema(c)
    yield c
    c.close()


def _seed_org(conn):
    """主体・2つの group（育苗部⊃防除課）・project・worker 群・役割を置く。

    組織構造:
        subject/1 ─has_role→ role/1（事業主）─holds→ worker/6（松山浩士）
        group/1（育苗部）─contains→ group/2（防除課）
        group/1 ─has_role→ role/2（育苗部長）─holds→ worker/6
        group/2 ─has_role→ role/3（防除課長）─holds→ worker/7（志保）
        project/1（新圃場開拓）─has_role→ role/4（PM）─holds→ worker/6
        worker/6（松山浩士）は 事業主・育苗部長・PM を**3重**に保持
        group/1 ─member_of→ worker/6, worker/7
        group/1 ─lead→ worker/6
    """
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "subject", "1", "三果倉")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "group", "1", "育苗部")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "group", "2", "防除課")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "project", "1", "新圃場開拓")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "worker", "6", "松山浩士")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "worker", "7", "志保")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "1", "事業主")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "2", "育苗部長")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "3", "防除課長")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "role", "4", "PM")

    # 主体→事業主
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "subject", "1", "has_role",
                           SUBJECT, DOMAIN, "role", "1")
    entity.assign_role(conn, SUBJECT, DOMAIN, "1", "6")
    # 育苗部⊃防除課（contains・木）
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "group", "1", "contains",
                           SUBJECT, DOMAIN, "group", "2")
    # 育苗部→育苗部長→松山浩士
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "group", "1", "has_role",
                           SUBJECT, DOMAIN, "role", "2")
    entity.assign_role(conn, SUBJECT, DOMAIN, "2", "6")
    # 防除課→防除課長→志保
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "group", "2", "has_role",
                           SUBJECT, DOMAIN, "role", "3")
    entity.assign_role(conn, SUBJECT, DOMAIN, "3", "7")
    # プロジェクト→PM→松山浩士
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "project", "1", "has_role",
                           SUBJECT, DOMAIN, "role", "4")
    entity.assign_role(conn, SUBJECT, DOMAIN, "4", "6")
    # 育苗部の所属（member_of）と組織長（lead）
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "group", "1", "member_of",
                           SUBJECT, DOMAIN, "worker", "6")
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "group", "1", "member_of",
                           SUBJECT, DOMAIN, "worker", "7")
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "group", "1", "lead",
                           SUBJECT, DOMAIN, "worker", "6")


# ── Layer A: 組織構造 ────────────────────────────────────────────

def test_a1_list_roles_from_node(conn):
    """A1: list_roles は任意ノード（group/project）から役割を辿る。"""
    _seed_org(conn)
    # 主体は事業主のみ
    subj = entity.list_roles(conn, SUBJECT, DOMAIN)
    assert [r["role"]["name"] for r in subj] == ["事業主"]
    # 育苗部は育苗部長
    dept = entity.list_roles(conn, SUBJECT, DOMAIN, from_entity_id="group",
                             from_object_id="1")
    assert [r["role"]["name"] for r in dept] == ["育苗部長"]
    assert dept[0]["holder"]["name"] == "松山浩士"
    # 防除課は防除課長
    sec = entity.list_roles(conn, SUBJECT, DOMAIN, from_entity_id="group",
                            from_object_id="2")
    assert [r["role"]["name"] for r in sec] == ["防除課長"]
    assert sec[0]["holder"]["name"] == "志保"
    # プロジェクトは PM
    proj = entity.list_roles(conn, SUBJECT, DOMAIN, from_entity_id="project",
                             from_object_id="1")
    assert [r["role"]["name"] for r in proj] == ["PM"]


def test_a2_roles_held_by_multiple(conn):
    """A2: 一人→N役割（二重/三重割り当て）。松山浩士=6 は3重。"""
    _seed_org(conn)
    held = entity.roles_held_by(conn, SUBJECT, DOMAIN, "6")
    names = {r["role"]["name"] for r in held}
    assert names == {"事業主", "育苗部長", "PM"}
    # 志保=7 は防除課長のみ
    held7 = entity.roles_held_by(conn, SUBJECT, DOMAIN, "7")
    assert {r["role"]["name"] for r in held7} == {"防除課長"}


def test_a3_org_tree(conn):
    """A3: contains の木（育苗部⊃防除課）を再帰展開。"""
    _seed_org(conn)
    tree = entity.org_tree(conn, SUBJECT, DOMAIN, "group", "1")
    assert tree["name"] == "育苗部"
    assert len(tree["children"]) == 1
    assert tree["children"][0]["name"] == "防除課"
    assert tree["children"][0]["children"] == []
    # 循環防止: 葉から辿っても無限ループしない
    leaf = entity.org_tree(conn, SUBJECT, DOMAIN, "group", "2")
    assert leaf["name"] == "防除課"
    assert leaf["children"] == []


def test_a4_org_members_and_leader(conn):
    """A4: member_of（所属）と lead（組織長）を解決。"""
    _seed_org(conn)
    om = entity.org_members(conn, SUBJECT, DOMAIN, "group", "1")
    assert {m["name"] for m in om["members"]} == {"松山浩士", "志保"}
    assert om["leader"]["name"] == "松山浩士"
    # 防除課（group/2）は所属・組織長未設定
    om2 = entity.org_members(conn, SUBJECT, DOMAIN, "group", "2")
    assert om2["members"] == []
    assert om2["leader"] is None


# ── Layer B: 責務を役割に ────────────────────────────────────────

def test_b1_responsible_role_column(conn):
    """B1: ensure_plan_schema が responsible_role_id 列を持つ。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schedule_def)").fetchall()}
    assert "responsible_role_id" in cols


def test_b2_add_schedule_def_responsible_role(conn):
    """B2: add_schedule_def が responsible_role_id を登録する。"""
    plan.add_schedule_def(
        conn, def_id="d1", net_id=SUBJECT, name="定植", target_key="subject",
        target_value="本圃", expected_state="P_transplant", cadence="oneshot",
        deadline_spec={"at": "2026-10-08T08:00"}, responsible_role_id="1")
    d = plan.get_schedule_def(conn, "d1")
    assert d["responsible_role_id"] == "1"
    # NULL（後方互換=主体の事業主）
    plan.add_schedule_def(
        conn, def_id="d2", net_id=SUBJECT, name="巡回点検", target_key="subject",
        expected_state="P_check", cadence="daily", deadline_spec={"time": "08:00"})
    assert plan.get_schedule_def(conn, "d2")["responsible_role_id"] is None


def test_b3_expected_state_carries_role(conn):
    """B3: expected_state_at が responsible_role_id を導出する。"""
    plan.add_schedule_def(
        conn, def_id="d1", net_id=SUBJECT, name="定植", target_key="subject",
        target_value="本圃", expected_state="P_transplant", cadence="oneshot",
        deadline_spec={"at": "2026-10-08T08:00"}, responsible_role_id="3")
    rows = plan.expected_state_at(conn, SUBJECT)
    d1 = next(r for r in rows if r["def_id"] == "d1")
    assert d1["responsible_role_id"] == "3"


def test_b4_cohort_responsible_role(conn):
    """B4: コホート（group_id 行群）の責務 = 同一 responsible_role_id。"""
    gid = "cohort-7"
    plan.add_schedule_def(
        conn, def_id="c7-1", net_id=SUBJECT, name="マルチ敷設", target_key="subject",
        target_value="本圃", expected_state="P_ready", cadence="oneshot",
        deadline_spec={"at": "2026-09-20T08:00"}, group_id=gid, group_name="第7期",
        seq=1, responsible_role_id="2")
    plan.add_schedule_def(
        conn, def_id="c7-2", net_id=SUBJECT, name="定植", target_key="subject",
        target_value="本圃", expected_state="P_transplant", cadence="oneshot",
        deadline_spec={"at": "2026-10-08T08:00"}, group_id=gid, group_name="第7期",
        seq=2, responsible_role_id="2")
    rows = plan.list_schedule_defs(conn, net_id=SUBJECT, group_id=gid)
    assert len(rows) == 2
    # コホートの責務担当役割は1つに束なる
    assert {r["responsible_role_id"] for r in rows} == {"2"}
