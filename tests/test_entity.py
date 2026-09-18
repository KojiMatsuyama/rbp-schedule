"""エンティティ層（humos_os.entity・v1.4.0）の単体テスト。

物理界の実在（4 層: 事業主体／事業ドメイン／エンティティ／オブジェクト）を
HUMOS に保持する層を検証する:
  E1  entity / entity_attribute / object_component / delegation の DDL（冪等）
  E2  entity（オブジェクト）の CRUD（upsert / get / list / delete）
  E3  属性の CRUD（set_attribute / get_attributes・二面性 attr_kind）
  E4  接地（ground）— 膜で記号界に接続（firing 行 + grounded フラグ）
  E5  ドメインDB との同期（sync_from_domain・属性の正はドメインDB）
  E6  主体隔離（subject_id で事業主体ごとに分離）
  E7  構成品（object_component）の CRUD
  E8  委任（delegation）の CRUD（sync_delegation / list_delegations）

4-level ID: subject_id ⊃ domain_id ⊃ entity_id（code）⊃ object_id。
  subject_id = "mikakura"（事業主体 三果倉）＝ net_id
  domain_id  = "iichigo"（事業ドメイン いちご）

venv の python で実行する（humos_os は pip パッケージとして個別インストール済み）:
    .venv/bin/python -m pytest tests/test_entity.py -v
"""

import sqlite3

import pytest

from humos_os import schema, entity

SUBJECT = "mikakura"   # 事業主体（三果倉）＝ net_id
DOMAIN = "iichigo"     # 事業ドメイン（いちご）


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    schema.ensure_schema(c)        # humos 5 テーブル（ground が使う）
    entity.ensure_entity_schema(c)  # entity + entity_attribute + object_component + delegation
    yield c
    c.close()


def test_e1_schema_idempotent(conn):
    entity.ensure_entity_schema(conn)  # 2 回目も冪等
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "entity" in tables
    assert "entity_attribute" in tables
    assert "object_component" in tables
    assert "delegation" in tables
    # 4-level ID の列が存在する
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entity)")}
    assert {"subject_id", "domain_id", "entity_id", "object_id"} <= cols


def test_e2_entity_crud(conn):
    # subject_id="mikakura" ⊃ domain_id="iichigo" ⊃ entity_id="field" ⊃ object_id="1"
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "1", "本圃")
    e = entity.get_entity(conn, SUBJECT, DOMAIN, "field", "1")
    assert e is not None and e["name"] == "本圃" and e["entity_id"] == "field"
    assert e["subject_id"] == SUBJECT and e["domain_id"] == DOMAIN
    assert e["grounded"] == 0

    # 更新（name 変更）
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "1", "本圃（改）")
    assert entity.get_entity(conn, SUBJECT, DOMAIN, "field", "1")["name"] == "本圃（改）"

    # 別エンティティ（薬剤商品）
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "pesticide", "P01", "ベルクート水和剤")
    assert len(entity.list_entities(conn, SUBJECT, DOMAIN)) == 2
    assert len(entity.list_entities(conn, SUBJECT, DOMAIN, entity_id="field")) == 1
    assert entity.list_entities(conn, SUBJECT, DOMAIN, entity_id="pesticide")[0]["object_id"] == "P01"

    # 削除
    entity.delete_entity(conn, SUBJECT, DOMAIN, "field", "1")
    assert entity.get_entity(conn, SUBJECT, DOMAIN, "field", "1") is None
    assert len(entity.list_entities(conn, SUBJECT, DOMAIN)) == 1


def test_e3_attribute_crud(conn):
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "1", "本圃")
    entity.set_attribute(conn, SUBJECT, DOMAIN, "field", "1", "spray_volume", 500.0,
                         attr_kind="requirement", source="fields")
    entity.set_attribute(conn, SUBJECT, DOMAIN, "field", "1", "area", "10a",
                         attr_kind="requirement", source="fields")
    # 状態側（二面性）
    entity.set_attribute(conn, SUBJECT, DOMAIN, "field", "1", "sprayed", "1",
                         attr_kind="state", source="manual")

    attrs = entity.get_attributes(conn, SUBJECT, DOMAIN, "field", "1")
    assert set(attrs.keys()) == {"spray_volume", "area", "sprayed"}
    assert attrs["spray_volume"]["attr_value"] == "500.0"
    assert attrs["spray_volume"]["attr_kind"] == "requirement"
    assert attrs["spray_volume"]["source"] == "fields"
    assert attrs["sprayed"]["attr_kind"] == "state"

    # 更新
    entity.set_attribute(conn, SUBJECT, DOMAIN, "field", "1", "spray_volume", 600.0,
                         attr_kind="requirement", source="fields")
    assert entity.get_attributes(conn, SUBJECT, DOMAIN, "field", "1")["spray_volume"]["attr_value"] == "600.0"


def test_e4_ground(conn):
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "1", "本圃")
    # 接地先プレース（humos の place）
    schema.ensure_schema(conn)
    from humos_os import humos
    humos.ensure_places(conn, {"P_spray_req": {"name": "散布要求", "kind": "state",
                                               "token_key": "spray_req"}})

    # 接地（膜で記号界に接続）— net_id = subject_id
    entity.ground(conn, SUBJECT, DOMAIN, "field", "1", "P_spray_req", "spray_req",
                  payload={"object_id": "1", "name": "本圃"},
                  event={"source": "ground", "place_id": "P_spray_req"})

    # 接地されたオブジェクトは grounded=1
    assert entity.get_entity(conn, SUBJECT, DOMAIN, "field", "1")["grounded"] == 1
    # 要求トークンがマーキングに置かれた（net_id = subject_id）
    marking = humos.get_marking(conn, SUBJECT)
    assert "spray_req" in marking
    # firing 行が記録された（リプレイ整合・E: プレフィックス）
    firing = humos.get_firing_log(conn, SUBJECT)
    assert any(f["transition_id"].startswith("E:spray_req") for f in firing)


def test_e5_sync_from_domain(conn):
    # ドメインDB（fields）の行をオブジェクトに同期（entity_id="field"）
    field_rows = [
        {"id": 1, "name": "育苗圃", "type": "nursery", "spray_volume": 50.0},
        {"id": 2, "name": "本圃", "type": "main", "spray_volume": 500.0},
    ]
    n = entity.sync_from_domain(conn, SUBJECT, DOMAIN, "field", field_rows,
                                id_key="id", name_key="name",
                                attr_map={"type": "type", "spray_volume": "spray_volume"})
    assert n == 2
    e = entity.get_entity(conn, SUBJECT, DOMAIN, "field", "2")
    assert e["name"] == "本圃"
    assert e["attrs"]["spray_volume"] == 500.0
    assert e["attrs"]["type"] == "main"
    # 属性テーブルにも反映（source=entity_id="field"）
    attrs = entity.get_attributes(conn, SUBJECT, DOMAIN, "field", "2")
    assert attrs["spray_volume"]["attr_value"] == "500.0"
    assert attrs["spray_volume"]["source"] == "field"

    # 薬剤（pesticides）の同期（attr_map なし=全列）
    pest_rows = [
        {"id": "P01", "name": "ベルクート水和剤", "formulation": "水和剤",
         "capacity": 500.0, "price": 12000},
    ]
    entity.sync_from_domain(conn, SUBJECT, DOMAIN, "pesticide", pest_rows,
                            id_key="id", name_key="name")
    pe = entity.get_entity(conn, SUBJECT, DOMAIN, "pesticide", "P01")
    assert pe["entity_id"] == "pesticide"
    assert pe["attrs"]["capacity"] == 500.0
    assert pe["attrs"]["price"] == 12000


def test_e6_subject_isolation(conn):
    # subject_id で隔離される（事業主体ごとに分離: 三果倉 vs 別主体）
    entity.upsert_entity(conn, "mikakura", "iichigo", "field", "1", "本圃")
    entity.upsert_entity(conn, "genki", "engine", "generator", "H1", "発電機H1")
    assert len(entity.list_entities(conn, "mikakura", "iichigo")) == 1
    assert len(entity.list_entities(conn, "genki", "engine")) == 1
    assert entity.list_entities(conn, "mikakura", "iichigo")[0]["object_id"] == "1"
    assert entity.list_entities(conn, "genki", "engine")[0]["object_id"] == "H1"
    # 同一 object_id でも subject が違えば別オブジェクト
    assert entity.get_entity(conn, "mikakura", "iichigo", "field", "1") is not None
    assert entity.get_entity(conn, "genki", "engine", "field", "1") is None


def test_e7_component_crud(conn):
    # 構成品（§1.5）— オブジェクトに所属（本圃 のベッド）
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "1", "本圃")
    entity.upsert_component(conn, SUBJECT, DOMAIN, "field", "1", "bed", "b1",
                            name="ベッド1", attrs={"length_m": 100.0})
    entity.upsert_component(conn, SUBJECT, DOMAIN, "field", "1", "bed", "b2",
                            name="ベッド2", attrs={"length_m": 90.0})

    comps = entity.get_components(conn, SUBJECT, DOMAIN, "field", "1")
    assert len(comps) == 2
    assert {c["comp_id"] for c in comps} == {"b1", "b2"}
    assert all(c["comp_type"] == "bed" for c in comps)
    b1 = next(c for c in comps if c["comp_id"] == "b1")
    assert b1["attrs"]["length_m"] == 100.0

    # 更新
    entity.upsert_component(conn, SUBJECT, DOMAIN, "field", "1", "bed", "b1",
                            name="ベッド1（改）", attrs={"length_m": 110.0})
    b1 = next(c for c in entity.get_components(conn, SUBJECT, DOMAIN, "field", "1")
              if c["comp_id"] == "b1")
    assert b1["name"] == "ベッド1（改）"
    assert b1["attrs"]["length_m"] == 110.0

    # 別オブジェクト（育苗圃）の構成品は分離
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "2", "育苗圃")
    entity.upsert_component(conn, SUBJECT, DOMAIN, "field", "2", "planter", "p1",
                            name="プランタン", attrs={"count": 12})
    assert len(entity.get_components(conn, SUBJECT, DOMAIN, "field", "2")) == 1
    assert entity.get_components(conn, SUBJECT, DOMAIN, "field", "2")[0]["comp_type"] == "planter"


def test_e8_delegation_crud(conn):
    # 委任（統一型付き関係）— 事業主体 → 圃場（own）・事業主体 → 従業者（employ）
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "subject", "1", "三果倉")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "field", "1", "本圃")
    entity.upsert_entity(conn, SUBJECT, DOMAIN, "worker", "1", "松山浩士")

    # own: 主体 → 圃場
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "subject", "1", "own",
                           SUBJECT, DOMAIN, "field", "1", granted_by="system")
    # employ: 主体 → 従業者
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "subject", "1", "employ",
                           SUBJECT, DOMAIN, "worker", "1", granted_by="system")

    dels = entity.list_delegations(conn, subject_id=SUBJECT,
                                   entity_id="subject", object_id="1")
    assert len(dels) == 2
    rels = {d["rel"] for d in dels}
    assert rels == {"own", "employ"}
    own = next(d for d in dels if d["rel"] == "own")
    assert own["to_entity_id"] == "field" and own["to_object_id"] == "1"

    # 冪等（同一エッジの再登録で増えない）
    entity.sync_delegation(conn, SUBJECT, DOMAIN, "subject", "1", "own",
                           SUBJECT, DOMAIN, "field", "1", granted_by="system")
    assert len(entity.list_delegations(conn, subject_id=SUBJECT,
                                       entity_id="subject", object_id="1")) == 2
