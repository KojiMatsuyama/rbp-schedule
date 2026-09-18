"""PLAN レイヤー（humos_os.plan）の単体テスト。

運航管理の「期待状態 Expected State(T) の正」を担う PLAN レイヤーを検証する:
  P1  schedule_def の DDL（冪等）
  P2  CRUD（add / get / update / list / delete・不正 cadence 拒否）
  P3  oneshot の逸脱判定（期限前・期限超過・到達済み）
  P4  grace（deviating: 期待超過・猶予内 / overdue: 硬期限超過）
  P5  daily / weekly / monthly / annual の期待時刻
  P6  interval / event（reference+offset・未設定=undefined）
  P7  プロジェクト（group_id + seq で reached 判定）
  P8  summarize（ステータス別集計）

venv の python で実行する（humos_os は pip パッケージとして個別インストール済み）:
    .venv/bin/python -m pytest tests/test_plan.py -v
"""

import sqlite3
from datetime import datetime

import pytest

from humos_os import plan


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    plan.ensure_plan_schema(c)
    yield c
    c.close()


def test_p1_schema_idempotent(conn):
    plan.ensure_plan_schema(conn)  # 2 回目も冪等
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "schedule_def" in tables


def test_p2_crud(conn):
    plan.add_schedule_def(conn, "d1", "mikakura", "散布", "field", "P_inv_result",
                          "oneshot", {"at": "2026-09-10T18:00"}, target_value="A-1")
    d = plan.get_schedule_def(conn, "d1")
    assert d is not None and d["name"] == "散布"
    assert d["deadline_spec"] == {"at": "2026-09-10T18:00"}
    plan.update_schedule_def(conn, "d1", grace_seconds=3600)
    assert plan.get_schedule_def(conn, "d1")["grace_seconds"] == 3600
    assert len(plan.list_schedule_defs(conn, net_id="mikakura")) == 1
    plan.delete_schedule_def(conn, "d1")
    assert plan.get_schedule_def(conn, "d1") is None
    with pytest.raises(ValueError):
        plan.add_schedule_def(conn, "bad", "mikakura", "x", "k", "P",
                              "hourly", {"time": "08:00"})


def test_p3_oneshot_status(conn):
    plan.add_schedule_def(conn, "o1", "mikakura", "散布", "field", "P_inv_result",
                          "oneshot", {"at": "2026-09-10T18:00"}, target_value="A-1")
    # 期限前・未到達 → on_track
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 1, 8, 50),
                               actual_state="P_input")[0]
    assert r["status"] == "on_track"
    assert r["slack_seconds"] > 0
    # 期限超過・未到達 → overdue
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 11, 0, 0),
                               actual_state="P_input")[0]
    assert r["status"] == "overdue"
    assert r["overdue_seconds"] > 0
    # 期限超過・到達済み → on_track
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 11, 0, 0),
                               actual_state="P_inv_result")[0]
    assert r["status"] == "on_track"
    assert r["reached"] is True


def test_p4_grace_deviating(conn):
    plan.add_schedule_def(conn, "g1", "mikakura", "散布", "field", "P_inv_result",
                          "oneshot", {"at": "2026-09-10T18:00"}, grace_seconds=3600)
    # E=18:00, D=19:00。18:30 未到達 → deviating
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 10, 18, 30),
                               actual_state="P_input")[0]
    assert r["status"] == "deviating"
    # 19:30 未到達 → overdue
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 10, 19, 30),
                               actual_state="P_input")[0]
    assert r["status"] == "overdue"


def test_p5_periodic_expected_time(conn):
    plan.add_schedule_def(conn, "day", "mikakura", "巡回", "field", "P_check",
                          "daily", {"time": "08:50"})
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 1, 8, 0),
                               actual_state=None)[0]
    assert r["expected_at"] == "2026-09-01 08:50:00"
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 1, 9, 0),
                               actual_state=None)[0]
    assert r["status"] == "overdue"

    plan.add_schedule_def(conn, "wk", "mikakura", "点検", "field", "P_check",
                          "weekly", {"time": "18:00"}, recurrence={"dow": 0})
    # 9/1 は木曜。dow=0（月曜）→ 現行月曜=8/31。
    wk = [x for x in plan.expected_state_at(
        conn, "mikakura", now=datetime(2026, 9, 1, 8, 0), actual_state=None)
        if x["def_id"] == "wk"][0]
    assert wk["expected_at"] == "2026-08-31 18:00:00"

    plan.add_schedule_def(conn, "mo", "mikakura", "報告", "field", "P_report",
                          "monthly", {"time": "12:00"}, recurrence={"dom": 15})
    mo = [x for x in plan.expected_state_at(
        conn, "mikakura", now=datetime(2026, 9, 1, 8, 0), actual_state=None)
        if x["def_id"] == "mo"][0]
    assert mo["expected_at"] == "2026-09-15 12:00:00"

    plan.add_schedule_def(conn, "yr", "mikakura", "点検", "field", "P_check",
                          "annual", {"time": "12:00"}, recurrence={"md": "03-15"})
    yr = [x for x in plan.expected_state_at(
        conn, "mikakura", now=datetime(2026, 9, 1, 8, 0), actual_state=None)
        if x["def_id"] == "yr"][0]
    assert yr["expected_at"] == "2026-03-15 12:00:00"


def test_p6_interval_event(conn):
    plan.add_schedule_def(conn, "iv", "mikakura", "油交換", "gen", "P_done",
                          "interval", {"offset": "7d"},
                          recurrence={"reference": "2026-09-01T00:00"})
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 3, 0, 0),
                               actual_state=None)[0]
    assert r["expected_at"] == "2026-09-08 00:00:00"
    assert r["status"] == "on_track"

    plan.add_schedule_def(conn, "ev", "mikakura", "散布", "field", "P_done",
                          "event", {"offset": "2h"}, recurrence={})
    ev = [x for x in plan.expected_state_at(
        conn, "mikakura", now=datetime(2026, 9, 3, 0, 0), actual_state=None)
        if x["def_id"] == "ev"][0]
    assert ev["status"] == "undefined"


def _add_edi_project(conn):
    ms = [
        ("e1", "SaaS EDI 設定", "P_edi_setup", "2026-09-15T18:00", 1),
        ("e2", "小売疎通テスト", "P_retail_test", "2026-09-20T18:00", 2),
        ("e3", "EDI 受注テスト", "P_order_test", "2026-09-25T18:00", 3),
        ("e4", "変換テスト", "P_conversion_test", "2026-09-30T18:00", 4),
        ("e5", "卸テスト", "P_wholesale_test", "2026-10-05T18:00", 5),
        ("e6", "サービスイン", "P_go_live", "2026-10-10T18:00", 6),
    ]
    for did, name, state, at, seq in ms:
        plan.add_schedule_def(conn, did, "mikakura", name, "project", state,
                              "oneshot", {"at": at}, target_value="edi-001",
                              group_id="g-edi-001", group_name="SaaS EDI 導入",
                              seq=seq)


def test_p7_project_group_reached(conn):
    _add_edi_project(conn)
    # 9/26（e3=9/25 期限超過）。actual=P_order_test（e3 到達）
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 26, 0, 0),
                               actual_state="P_order_test")
    by_id = {x["def_id"]: x for x in r}
    assert by_id["e1"]["status"] == "on_track"
    assert by_id["e3"]["status"] == "on_track"
    assert by_id["e4"]["status"] == "on_track"
    assert all(x["group_id"] == "g-edi-001" for x in r)
    assert all(x["group_name"] == "SaaS EDI 導入" for x in r)

    # actual=P_retail_test（e2 到達、e3 期限超過で未到達）→ e3 は overdue
    r = plan.expected_state_at(conn, "mikakura",
                               now=datetime(2026, 9, 26, 0, 0),
                               actual_state="P_retail_test")
    by_id = {x["def_id"]: x for x in r}
    assert by_id["e3"]["status"] == "overdue"
    assert by_id["e1"]["status"] == "on_track"
    assert by_id["e2"]["status"] == "on_track"
    assert all(by_id[f"e{i}"]["status"] == "on_track" for i in (4, 5, 6))


def test_p8_summarize(conn):
    _add_edi_project(conn)
    s = plan.summarize(conn, "mikakura", now=datetime(2026, 9, 26, 0, 0),
                       actual_state="P_retail_test")
    assert s["total"] == 6
    assert s["counts"]["overdue"] == 1
    assert s["counts"]["on_track"] == 5


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
