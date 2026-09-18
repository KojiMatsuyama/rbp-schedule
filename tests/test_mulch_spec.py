"""マルチ選定（T_mulch_select・task_id='mulch'）の 5 段 RBP パイプライン検証。

ユーザー指定（2026-09-07）の仕様を検証する:
  - 要求は**マルチ選定要求トークン**（事業主体のスケジュール起動イベントで発生）。
    payload = 事業主体 / 圃場（文脈）/ 定植 / ベッド延長 / 幅
  - **認知**: その要求トークンを認知する
  - **要求評価**: **定植白黒マルチグループ**が選択される（**ミラーID** 付き）
  - **仕様決定**: **白黒マルチの 列数・幅110cm・延べ長さ** が抽出される

データは本番 DB（data/stb.db）の圃場「本圃」(field_id=2) に対して検証する
（白黒 4 列=延べ460m・黒 2 列=延べ50m・幅110cm）。

要求トークンは 4-level 化された新形状（ts/真界_事業主体設計.md §5.2）:
  object=事業主体名 / subject_id="mikakura" / field_id+field_name（文脈）/ context="定植"。

venv の python で実行する（humos_os は pip パッケージとして個別インストール済み）:
    .venv/bin/python -m pytest tests/test_mulch_spec.py -v
"""

import os
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

import mulch  # noqa: E402

HONPO_1_FIELD_ID = 2  # 本圃
SUBJECT_NAME = "三果倉"
SUBJECT_ID = "mikakura"


@pytest.fixture
def conn():
    c = mulch.connect()
    yield c
    c.close()


def test_cognition_reads_token_payload(conn):
    """認知: マルチ選定要求トークン（事業主体/圃場/定植/ベッド延長/幅）を認知する。"""
    token = mulch.build_token(conn, HONPO_1_FIELD_ID)
    # 新形状: object=事業主体名 / subject_id / field_name（文脈）/ context=定植 / 延長 / 幅
    assert token["object"] == SUBJECT_NAME
    assert token["subject_id"] == SUBJECT_ID
    assert token["field_name"] == "本圃"
    assert token["context"] == "定植"
    assert token["total_length_m"] > 0          # ベッド延長
    assert token["width_cm"] == 110.0           # 幅（primary=白黒の資材幅）
    assert token["beds"], "beds が空"

    demand = mulch.perceive_from_token(conn, token)
    # 認知は beds を mulch_type でグルーピング（本圃=白黒グループ+黒グループ）。
    types = {r["type"] for r in demand["requirements"]}
    assert "白黒" in types
    # primary（延べ長さ最大）= 白黒グループ（延べ460m・4列・幅110cm）。
    primary = demand["requirements"][0]
    assert primary["type"] == "白黒"
    assert primary["vector"] == [1, 0, 0]
    assert primary["columns"] == 4
    assert primary["total_length_m"] == 460.0
    assert primary["width_cm"] == 110.0


def test_evaluation_selects_group_with_mirror(conn):
    """要求評価: 定植白黒マルチグループが選択され、ミラーID=1.0 が付与される。"""
    token = mulch.build_token(conn, HONPO_1_FIELD_ID)
    demand = mulch.perceive_from_token(conn, token)
    primary = demand["requirements"][0]
    sel = mulch.evaluate(conn, primary)
    assert sel["group_name"] == "定植白黒マルチグループ"
    assert sel["group_id"] == "MULCH-01"
    assert sel["context"] == "定植"
    assert sel["mirror_id"] == pytest.approx(1.0)
    assert sel["status"] == "MATCH"


def test_decision_extracts_spec(conn):
    """仕様決定: 白黒マルチの 4列・幅110cm・延べ460m が抽出される。"""
    token = mulch.build_token(conn, HONPO_1_FIELD_ID)
    demand = mulch.perceive_from_token(conn, token)
    primary = demand["requirements"][0]
    sel = mulch.evaluate(conn, primary)
    spec = mulch.decide_spec(conn, primary, sel)
    assert spec["material_name"] == "白黒マルチ"
    assert spec["columns"] == 4
    assert spec["width_cm"] == 110.0
    assert spec["total_length_m"] == 460.0
    assert spec["mirror_id"] == pytest.approx(1.0)
    assert spec["group_name"] == "定植白黒マルチグループ"


def test_full_pipeline_field(conn):
    """全パイプライン（field_id 経由）: 認知→評価→決定→投射→作動。"""
    r = mulch.prescribe(conn, field_id=HONPO_1_FIELD_ID)
    # 認知
    assert r["demand"]["field_name"] == "本圃"
    # 要求評価（primary=白黒）
    assert r["selection"]["group_name"] == "定植白黒マルチグループ"
    assert r["selection"]["mirror_id"] == pytest.approx(1.0)
    # 仕様決定
    assert r["spec"]["material_name"] == "白黒マルチ"
    assert r["spec"]["columns"] == 4
    assert r["spec"]["width_cm"] == 110.0
    assert r["spec"]["total_length_m"] == 460.0
    # 投射（選定資材名で手順書合成）
    assert r["projection"]["composed"].startswith("本圃：白黒マルチ")
    # 作動（ドライラン）
    assert r["actuation"]["sent"] is False


def test_full_pipeline_token_driven(conn):
    """全パイプライン（トークン駆動）: ネットが T_mulch_select を発火する経路。"""
    token = mulch.build_token(conn, HONPO_1_FIELD_ID)
    r = mulch.prescribe(conn, token=token)
    assert r["demand"]["field_name"] == "本圃"
    assert r["selection"]["group_name"] == "定植白黒マルチグループ"
    assert r["selection"]["mirror_id"] == pytest.approx(1.0)
    assert r["spec"]["material_name"] == "白黒マルチ"
    assert r["spec"]["columns"] == 4
    assert r["spec"]["width_cm"] == 110.0
    assert r["spec"]["total_length_m"] == 460.0
    # 黒グループも all_specs に含まれる（本圃=白黒+黒）。
    all_types = {s["requirement"]["type"] for s in r["all_specs"]}
    assert "白黒" in all_types and "黒" in all_types


def test_engine_judge_consistent(conn):
    """RBP エンジン（ミラーID・資材選定）と仕様決定が一致する。"""
    r = mulch.prescribe(conn, field_id=HONPO_1_FIELD_ID)
    best = r["decision"].get("best")
    assert best is not None
    assert best["mirrorId"] == pytest.approx(1.0)
    assert best["pesticides"][0]["name"] == "白黒マルチ"
    # エンジンのミラーID と 要求評価のミラーID が一致。
    assert best["mirrorId"] == pytest.approx(r["selection"]["mirror_id"])
