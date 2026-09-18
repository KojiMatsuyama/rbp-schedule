#!/usr/bin/env python3
"""db_setup.py — SQLite DBの作成・初期データ投入スクリプト。
病害虫はインライン DISEASES_SEED（唯一無二の正）、
薬剤・評価BOXは既存のJSONファイルからデータを抽出してSQLiteに格納する。
"""
import json
import os
import sqlite3
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pesticides (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    activeIngredient TEXT,
    category TEXT,
    targetVector TEXT,       -- JSON array of ints
    targetNames TEXT,        -- JSON array of strings
    phiDays REAL,
    mixingRestriction TEXT,
    mixingBanTargets TEXT,   -- JSON array of strings
    maxApplications REAL,
    toxicityClass TEXT,
    system TEXT,
    systemCode TEXT,
    dilutionRate TEXT,
    -- 商品マスター（買う・使うために必要な情報）
    brand TEXT,             -- メーカー（住園化学）
    formulation TEXT,       -- 剤形（技術的形態: 水和剤/乳剤/フロアブル…）
    packaging TEXT,         -- 梱包形態（容器: 袋/ボトル/缶/箱/本）
    contentForm TEXT,       -- 内容の形（物理状態: 液体/粉/顆粒/タブレット/粒）
    applicationRate REAL,   -- 散布量（L/10a）→ 投射: 必要量計算
    capacity REAL,          -- 容量（1個あたりの量, 5）→ 決定: 何個必要
    packSize REAL,          -- 販売単位（何個で売っているか, 1=単体）
    packUnit TEXT,          -- 容量単位（kg/g/L/mL）
    price REAL              -- 単価（円/販売単位）
);

CREATE TABLE IF NOT EXISTS diseases (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('disease', 'pest')),
    icon TEXT
);

CREATE TABLE IF NOT EXISTS eval_boxes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vector TEXT NOT NULL     -- JSON array of ints
);

CREATE TABLE IF NOT EXISTS eval_boxes_custom (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vector TEXT NOT NULL     -- JSON array of ints
);

CREATE TABLE IF NOT EXISTS spray_history (
    date TEXT PRIMARY KEY,
    pests TEXT NOT NULL,     -- JSON array of strings
    vector TEXT NOT NULL,    -- JSON array of ints
    field_id INTEGER REFERENCES fields(id)
);

CREATE TABLE IF NOT EXISTS spray_schedule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_date   TEXT    NOT NULL,          -- 予定日 YYYY-MM-DD
    actual_date     TEXT,                      -- 実際の実施日（NULL=未実施）
    status          TEXT    NOT NULL DEFAULT 'scheduled'
                        CHECK(status IN ('scheduled', 'done', 'missed', 'rescheduled')),
    trigger_type    TEXT    NOT NULL DEFAULT 'cycle'
                        CHECK(trigger_type IN ('cycle', 'observation', 'forecast')),
    trigger_ref     TEXT,                      -- 参照元ID（EVAL_BOX IDなど）
    eval_box_id     TEXT REFERENCES eval_boxes(id),
    rb_out_json     TEXT,                      -- RBP_OUTのJSON（要求評価+仕様決定の結果）
    set_ids         TEXT    NOT NULL,          -- JSON: ["セット1", "セット7"]
    pesticide_ids   TEXT    NOT NULL,          -- JSON: ["P40", "P42"]
    operator        TEXT,                      -- 担当者
    assignee_ids    TEXT,                      -- JSON: [worker_id, ...] 担当者（複数）
    weather         TEXT,                      -- 天候（晴/曇/雨）
    notes           TEXT,                      -- 備考
    field_id        INTEGER REFERENCES fields(id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst'))
);

-- 圃場マスター（育苗／本圃）。面積は数値+単位で保持。
-- タイムスタンプは NOT NULL DEFAULT(datetime('now','jst')) を使わない:
-- 'jst' は無効なSQLite修飾子でNULLとなりINSERTが落ちるため、投入側が明示指定する。
CREATE TABLE IF NOT EXISTS fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK(type IN ('nursery', 'main')),   -- 育苗 / 本圃
    area_value REAL,                                          -- 面積（数値、NULL可）
    area_unit TEXT CHECK(area_unit IN ('m2', 'tan', 'tsubo')),
    spray_rate REAL,          -- 散布率 (L/10a) — 圃場タイプ別の接地ナレッジ（任意・参考）
    spray_volume REAL,        -- 散布量 (L) — 圃場ごとに直接接地（緒言・圃場オブジェクト属性）
    operator_id INTEGER REFERENCES operators(id),  -- 権利者（事業主体・オントロジー根底改修）
    created_at TEXT,
    updated_at TEXT
);

-- ベッド台帳（beds）— 圃場（fields）の関連テーブル（本舗の構成品）。
-- 一列ずつの「長さ」が必要（マルチ敷設の認知/要求評価の緒言・要求ベクトルの軸）。
-- 病害虫DB（diseases）が薬剤選定の認知の緒言なら、ベッド台帳がマルチ選定の
-- 認知の緒言（どのベッドにマルチが必要か＝要求ベクトルの軸を定義）。
CREATE TABLE IF NOT EXISTS beds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    field_id INTEGER NOT NULL REFERENCES fields(id),
    name TEXT NOT NULL,                       -- ベッド名（例: 本圃-1列）
    seq INTEGER,                              -- 列番号（圃場内 1,2,3…）
    length_m REAL,                            -- 一列の長さ（m）
    width_m REAL,                             -- 列幅（m）
    mulch_type TEXT,                          -- 必要なマルチ種別（白黒/黒/透明…）
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_beds_field ON beds(field_id);

-- マルチ資材DB（mulch_materials）— マルチ選定の仕様決定の緒言（候補プール）。
-- pesticides が薬剤選定の仕様決定の緒言なら、mulch_materials がマルチ選定の
-- 仕様決定の緒言（どのマルチ資材を選ぶか＝候補プール）。
CREATE TABLE IF NOT EXISTS mulch_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                       -- 例: 白黒マルチ
    type TEXT,                                -- 白黒 / 黒 / 透明 …
    width_cm REAL,                            -- 幅（cm）
    hole_interval_cm REAL,                    -- 穴の間隔（cm）
    length_cm REAL,                           -- 長さ（cm）
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- マルチ在庫（mulch_inventory）— マルチ在庫確認の緒言（数量型）。
-- 薬剤の inventory（ロット単位）と異なり、マルチは数量単位（m²/ロール数）。
CREATE TABLE IF NOT EXISTS mulch_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER REFERENCES mulch_materials(id),
    field_id INTEGER REFERENCES fields(id),
    quantity REAL NOT NULL DEFAULT 0,         -- 数量
    unit TEXT NOT NULL DEFAULT 'm2',          -- 単位（m2 / roll）
    lot_number TEXT,
    expiry_date TEXT,
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mulch_inventory_material ON mulch_inventory(material_id);

CREATE INDEX IF NOT EXISTS idx_spray_schedule_date ON spray_schedule(schedule_date);
CREATE INDEX IF NOT EXISTS idx_spray_schedule_status ON spray_schedule(status);
CREATE INDEX IF NOT EXISTS idx_spray_schedule_eval_box ON spray_schedule(eval_box_id);

-- 圃場マスター（fields）と同型の名前マスター。transplant_schedule.judge_location
-- の候補を管理（検鏡場所）。唯一無二の正は DB の inspection_locations テーブル。
-- DDL は server.py の init_db() と同一（既存の二重管理の流儀）。
CREATE TABLE IF NOT EXISTS inspection_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_inspection_locations_name ON inspection_locations(name);

-- 事業主体・実働主体マスター（operators）— 主体（subject）と実働（operator）。
-- 事業主体（三果倉）は要求の発生源（接地の根・4-level ID の subject_id）。
-- DDL は server.py の init_db() と同一（既存の二重管理の流儀）。
CREATE TABLE IF NOT EXISTS operators (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,       -- 主体/実働名（三果倉 等）
    type        TEXT    NOT NULL CHECK(type IN ('subject', 'operator')),
    owner_name  TEXT,                          -- 所有者（個人名・例 松山浩士）
    notes       TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_operators_type ON operators(type);

-- 組織マスター（organizations）— 外部組織（判定/管理）。自由記述の組織名をオブジェクト化。
-- 那賀地方いちご生産組合連合会（判定）/ JA紀の里（管理）等。role で役割を区別。
CREATE TABLE IF NOT EXISTS organizations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL UNIQUE, -- 組織名
    role              TEXT,                    -- 役割（judge=判定 / manage=管理 / supplier=供給…）
    person_in_charge  TEXT,                    -- 担当者
    tel               TEXT,
    fax               TEXT,
    notes             TEXT,
    created_at        TEXT,
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations(name);

-- 役割（roles）— 位置的角色（事業主・店長・課長…）のノード。第一級エンティティ
-- （entity_id='role'）。紐付け（保持者）は HUMOS の delegation に置く。
-- 詳細は server.py init_db の同 DDL・ts/真界_事業主体設計.md §2.6 参照。
CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,   -- 事業主 / 店長 / 部門長 / 課長…
    subject_id  TEXT    NOT NULL DEFAULT 'mikakura',
    notes       TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_roles_name ON roles(name);

-- 従業者（workers）— 持ち込み・作業の「誰」。DC の external_vendors（委託先）と同型。
-- 検鏡の持ち込み担当・定植担当をタスク単位で割り当てる。
CREATE TABLE IF NOT EXISTS workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT,
    phone TEXT,
    org TEXT,
    operator_id INTEGER REFERENCES operators(id),  -- 所属（事業主体・オントロジー根底改修）
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workers_name ON workers(name);

-- コホート（cohorts）— イチゴ栽培サイクル。1 コホート = 1 大きなまとまり
-- （育苗→熱消毒→定植→収穫→廃棄 の年をまたぐサイクル）= 1 プロジェクト。
-- 命名は「第N期」（通算番号・年またぎに強い）。収穫年・育苗開始年月は属性。
-- 第N期 = 収穫年（西暦）で 1 対 1（例: 第5期 = 令和8年(2026)収穫・2025-11 育苗開始）。
CREATE TABLE IF NOT EXISTS cohorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    period_no INTEGER,
    harvest_year INTEGER,
    seedling_start TEXT,
    field_id INTEGER REFERENCES fields(id),
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cohorts_name ON cohorts(name);

-- 定植暦（transplant_schedule）— 育苗圃苗の「花芽分化確認」を暦として管理。
-- 防除暦（spray_schedule）と同一パターン（§2.3 の流儀: 暦は1枚・表に cadence で区別）。
-- 完了判定は**外部組織（那賀地方いちご生産組合連合会）の判定**＝STN の
-- P_external_wait（外部待ち）プレースに対応。判定記録（judge_*）が reached を決める。
-- cohort_id ありはプロジェクト（検鏡 seq 鎖 + 定植）、なしは月束ねの定常扱い。
-- DDL は server.py の init_db() と同一（既存の二重管理の流儀）。
CREATE TABLE IF NOT EXISTS transplant_schedule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_date   TEXT    NOT NULL,          -- 花芽分化確認予定日 YYYY-MM-DD
    start_time      TEXT,                      -- 開始時刻 HH:MM（NULL=未指定）
    end_time        TEXT,                      -- 終了時刻 HH:MM（NULL=未指定）
    judge_date      TEXT,                      -- 判定日（外部組織が判定した日・NULL=未判定）
    judge_result    TEXT,                      -- 判定結果（分化あり/分化なし）
    judge_org       TEXT,                      -- 判定組織（既定: 那賀地方いちご生産組合連合会）
    judge_location  TEXT,                      -- 検査場所（JA紀の里地域本部 / ふるさとセンター）
    status          TEXT    NOT NULL DEFAULT 'scheduled'
                    CHECK(status IN ('scheduled', 'confirmed', 'pending', 'missed')),
    notes           TEXT,                      -- 備考（品種・株数・圃場など）
    field_id        INTEGER REFERENCES fields(id),
    cohort_id       INTEGER REFERENCES cohorts(id),  -- 属するコホート（NULL=未割当）
    seq             INTEGER,                   -- コホート内の検鏡順（1,2,3…）
    is_final        INTEGER DEFAULT 0,         -- 最終検鏡フラグ（1=最終）
    assignee_id     INTEGER REFERENCES workers(id),  -- 持ち込み担当（従業者）
    assignee_ids    TEXT,                            -- JSON: [worker_id, ...] 担当（複数）
    judge_org_id    INTEGER REFERENCES organizations(id),  -- 判定組織（オントロジー根底改修）
    created_at      TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_transplant_schedule_date ON transplant_schedule(schedule_date);
CREATE INDEX IF NOT EXISTS idx_transplant_schedule_status ON transplant_schedule(status);
CREATE INDEX IF NOT EXISTS idx_transplant_schedule_cohort ON transplant_schedule(cohort_id);

-- 定植予定（transplant_plan）— コホートの定植日・担当・実績。
-- 1 コホート 1 定植。ブリッジが判定から自動導出した日付を手動で上書きする。
CREATE TABLE IF NOT EXISTS transplant_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    schedule_date TEXT,
    assignee_id INTEGER REFERENCES workers(id),
    assignee_ids TEXT,   -- JSON: [worker_id, ...] 担当（複数）
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'done', 'missed')),
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- 熱消毒予定（heat_schedule）— コホートのベッド準備（熱消毒）の着手日・期限日・担当・実績。
-- 1 コホート 1 熱消毒。定植の1か月前に着手し3週間以上実施する前段フェーズ。
-- 日付は手動指定（自動導出なし）。
CREATE TABLE IF NOT EXISTS heat_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    start_date TEXT,                        -- 熱消毒着手日 YYYY-MM-DD（例: 第5期=2026-08-16）
    deadline_date TEXT,                     -- 完了期限日 YYYY-MM-DD（手動指定）
    assignee_id INTEGER REFERENCES workers(id),
    assignee_ids TEXT,                      -- JSON: [worker_id, ...] 担当（複数）
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'started', 'done', 'missed')),
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_transplant_plan_cohort ON transplant_plan(cohort_id);

-- ── 境界言語DSL 自動導出用テーブル（共通上位形式・STB）─────────────
-- DC（発電機オイル）と同一の rbp_* 知識テーブル。共通上位形式BNF.txt の
-- <Domain-Profile> を STB 値（specbridge=present / candidate-selection /
-- exact-match / level-ascending）で満たす。判断知識（ブリッジ・スコア・
-- BOX・認知軸・ゲート・投射）を形式知として DB に格納し、derive_bnf.py /
-- rbp_engine.py が DB から境界言語・RBP 行列値を自動導出する（クローズドループ）。

CREATE TABLE IF NOT EXISTS rbp_domain (
    domain_id        TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    layer_specbridge TEXT NOT NULL DEFAULT 'absent'
                           CHECK(layer_specbridge IN ('present','absent')),
    fold_order       TEXT NOT NULL DEFAULT 'declaration-order'
                           CHECK(fold_order IN ('level-ascending','declaration-order')),
    -- <Spec-Profile>.kind（共通上位形式BNF 第1章 D3）: 仕様決定の意思決定戦略。
    -- candidate-selection=候補プールから最良セット選択（STB）/
    -- condition-decision=候補を選ばず条件のみ判定（DC）。
    spec_kind        TEXT NOT NULL DEFAULT 'condition-decision'
                           CHECK(spec_kind IN ('candidate-selection','condition-decision')),
    -- <Spec-Profile>.selection（candidate-selection のみ）。候補プールの指針:
    -- どの表（table）・何個（count）・何（label）を候補として選ぶか。
    candidate_pool       TEXT,
    candidate_pool_label TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rbp_perception_axis (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id      TEXT NOT NULL REFERENCES rbp_domain(domain_id),
    task_id        TEXT,                      -- タスク=業務単位（NULL=未割当・後方互換）
    axis_name      TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    dims           TEXT NOT NULL,
    dim_type       TEXT NOT NULL CHECK(dim_type IN ('bit','real')),
    norm_source    TEXT NOT NULL,
    norm_basis     TEXT,
    check_all_zero TEXT NOT NULL CHECK(check_all_zero IN ('pass','fail')),
    lead           REAL,
    threshold      REAL,
    UNIQUE(domain_id, axis_name)
);

CREATE TABLE IF NOT EXISTS rbp_eval_box (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id  TEXT NOT NULL REFERENCES rbp_domain(domain_id),
    task_id    TEXT,                          -- タスク=業務単位（NULL=未割当・後方互換）
    box_id     TEXT NOT NULL,
    box_name   TEXT NOT NULL,
    condition  TEXT NOT NULL
                   CHECK(condition IN ('exact','subset','any-dim-ge','all-dim-lt')),
    threshold  REAL,
    members    TEXT,
    UNIQUE(domain_id, box_id)
);

-- STB のブリッジは述語ベース（rule_json）。DC の閾値ベース（dim/open_at）と
-- 共存させるため、dim/open_at は NULL 可、rule_json を追加（共通上位形式の
-- <SpecBridge-Rule> / <BRIDGE-Def> 対応）。
CREATE TABLE IF NOT EXISTS rbp_bridge (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id  TEXT NOT NULL REFERENCES rbp_domain(domain_id),
    task_id    TEXT,                          -- タスク=業務単位（NULL=未割当・後方互換）
    bridge_id  TEXT NOT NULL,
    level      INTEGER NOT NULL,
    dim        INTEGER,
    open_at    REAL,
    rule_json  TEXT,
    UNIQUE(domain_id, bridge_id)
);

CREATE TABLE IF NOT EXISTS rbp_spec_condition (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id     TEXT NOT NULL REFERENCES rbp_domain(domain_id),
    task_id       TEXT,                       -- タスク=業務単位（NULL=未割当・後方互換）
    cond_name     TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK(kind IN ('variable','fixed')),
    source        TEXT,
    source_ref    TEXT,
    decision_rule TEXT,
    rule_json     TEXT,
    seq           INTEGER NOT NULL,
    UNIQUE(domain_id, cond_name)
);

CREATE TABLE IF NOT EXISTS rbp_spec_gate (
    domain_id  TEXT PRIMARY KEY REFERENCES rbp_domain(domain_id),
    task_id    TEXT,                          -- タスク=業務単位（NULL=未割当・後方互換）
    pre        TEXT,
    open_expr  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rbp_projection (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id   TEXT NOT NULL REFERENCES rbp_domain(domain_id),
    task_id     TEXT,                         -- タスク=業務単位（NULL=未割当・後方互換）
    output_name TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'pure-mapping',
    template    TEXT NOT NULL,
    UNIQUE(domain_id, output_name)
);

-- タスク（業務）単位で知識を持つための登録テーブル。
-- 普遍構造 T = {認知,評価,決定,投射} + 作動 に合わせ、タスクの知識=5段の行集合。
-- 各 rbp_* の task_id がこの task_id を参照する（NULL=未割当）。
CREATE TABLE IF NOT EXISTS rbp_task (
    task_id     TEXT PRIMARY KEY,
    domain_id   TEXT REFERENCES rbp_domain(domain_id),
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- 作動（=T の正体・物理界的作動）。タスクごとに「最終到達点のやること」。
CREATE TABLE IF NOT EXISTS rbp_actuation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL REFERENCES rbp_task(task_id),
    name        TEXT NOT NULL,
    handler     TEXT,
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- 仕様決定（SPEC）の導出結果を格納・メンテナンスするテーブル（STB版）。
-- DC の rbp_spec_decision（発電機×評価日・可変部3）に同型だが、STB は
-- 候補選択（candidate-selection）なので「導出された処方スナップショット」を
-- 保持する。UNIQUE(field_id, as_of): 圃場×評価日 1件。再発火は上書き（冪等）。
-- 知識（どう導出するか）は rbp_spec_condition / rbp_bridge、ここは
-- 「導出された値」のスナップショット。
CREATE TABLE IF NOT EXISTS rbp_spec_decision (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    field_id         INTEGER REFERENCES fields(id),
    as_of            TEXT NOT NULL,             -- 評価時点 YYYY-MM-DD
    entry_vector     TEXT,                      -- 入力: 10次元 0/1 認知ベクトル（JSON）
    eval_box_id      TEXT,                      -- 評価BOX（完全一致 / UNDEFINED）
    status           TEXT,                      -- SUCCESS / NO_PESTICIDE_DEFINED /
                                                --   ALL_BLOCKED_BY_CONSTRAINTS
    best_set         TEXT,                      -- 選択された候補セット（JSON）
    best_score       REAL,                      -- 選択候補の total_score
    mirror_id        REAL,                      -- 選択候補の Mirror-ID（cosine）
    alternatives     TEXT,                      -- 代替案（JSON）
    bridge_trace     TEXT,                      -- 各薬剤のブリッジ軌跡（JSON）
    spec_json        TEXT,                      -- 処方 dict 全体（JSON・fidelity）
    source           TEXT
                       CHECK(source IN ('engine','fallback','manual','backfill')),
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(field_id, as_of)
);

CREATE INDEX IF NOT EXISTS idx_rbp_spec_decision_field ON rbp_spec_decision(field_id);
CREATE INDEX IF NOT EXISTS idx_rbp_spec_decision_asof  ON rbp_spec_decision(as_of);

-- ACTUATIONテーブル群（作動の認識）
CREATE TABLE IF NOT EXISTS external_vendors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_key    TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    specialty     TEXT,
    contact_name  TEXT,
    contact_phone TEXT,
    contact_email TEXT,
    sla_notes     TEXT,
    status        TEXT DEFAULT 'active'
                        CHECK(status IN ('active','inactive')),
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actuation_channels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_key  TEXT UNIQUE NOT NULL,
    channel_type TEXT NOT NULL
                       CHECK(channel_type IN ('slack','email','phone','paper')),
    target       TEXT,
    config       TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actuation_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id   TEXT NOT NULL REFERENCES rbp_domain(domain_id),
    trigger     TEXT NOT NULL,
    actor       TEXT,
    actor_type  TEXT CHECK(actor_type IN ('vendor','employee')),
    channel_key TEXT REFERENCES actuation_channels(channel_key),
    payload     TEXT,
    UNIQUE(domain_id, trigger, channel_key)
);

-- =====================================================================
-- HUMOS（土壌 / 状態空間管理基盤）— 真界ループの状態空間（DC/STB 共通OS）
-- 5 テーブル（place / token / marking / firing / writing）。humos_os.schema と
-- 同一の DDL（二重管理の最小化のため humos_os.schema.SCHEMA_SQL を参照）。
-- =====================================================================
CREATE TABLE IF NOT EXISTS place (
    place_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'state'
               CHECK(kind IN ('state','gate','external_wait','terminal')),
    capacity   INTEGER,
    token_key  TEXT
);
CREATE TABLE IF NOT EXISTS token (
    net_id     TEXT NOT NULL,
    token_id   TEXT NOT NULL,
    kind       TEXT,
    payload    TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY(net_id, token_id)
);
CREATE TABLE IF NOT EXISTS marking (
    net_id     TEXT NOT NULL,
    place_id   TEXT NOT NULL,
    token_id   TEXT NOT NULL,
    entered_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY(net_id, place_id, token_id)
);
CREATE TABLE IF NOT EXISTS firing (
    net_id           TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    transition_id    TEXT NOT NULL,
    fired_at         TEXT DEFAULT (datetime('now')),
    consumed         TEXT,
    produced         TEXT,
    triggering_event TEXT,
    PRIMARY KEY(net_id, seq)
);
CREATE TABLE IF NOT EXISTS writing (
    net_id           TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    operator_id      TEXT,
    operator_kind    TEXT,
    action           TEXT,
    payload          TEXT,
    related_place_id TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    PRIMARY KEY(net_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_firing_net  ON firing(net_id, seq);
CREATE INDEX IF NOT EXISTS idx_writing_net ON writing(net_id, seq);
CREATE INDEX IF NOT EXISTS idx_marking_net ON marking(net_id);
"""


def migrate_add_field_id(conn):
    """既存DBへの field_id カラム追加（idempotent）。
    CREATE TABLE IF NOT EXISTS では既存テーブルにカラムが追えないため、
    PRAGMA table_info で確認し無ければ ALTER で追加する。
    fields テーブル作成の後に呼ぶこと（参照先が先に存在する必要がある）。
    inventory は server.py 側で作られるため存在しない場合をスキップする。"""
    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    for table in ("spray_schedule", "spray_history", "inventory"):
        if table not in tables:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "field_id" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN field_id INTEGER REFERENCES fields(id)"
            )
            print(f"  migrate: ALTER TABLE {table} ADD COLUMN field_id")
    conn.commit()


# 病害虫DB（唯一無二の正）。手編集していた複製 data/diseases.json は廃止し、
# このインライン定義が bootstrap のシード元になった。
# 静的フロントエンド向けの data/diseases.js は、scripts/export_diseases.py が
# この正（DB）から生成するスナップショット（手編集禁止）。
# 次元id 0〜9 は RBP の10次元ベクトルとの対応（perception.py 等が参照）。
DISEASES_SEED = [
    (0, "炭疽病", "disease", "🍅"),
    (1, "灰色かび病", "disease", "🌫️"),
    (2, "うどんこ病", "disease", "🍚"),
    (3, "ナミハダニ", "pest", "🕷️"),
    (4, "ハスモンヨトウ", "pest", "🦋"),
    (5, "オオタバコガ", "pest", "🐛"),
    (6, "ミカンキイロアザミウマ", "pest", "🪳"),
    (7, "ワタアブラムシ", "pest", "🐌"),
    (8, "アブラムシ", "pest", "🐜"),
    (9, "コナジラミ", "pest", "🪽"),
]


# 圃場マスターの初期シード（唯一無二の正は DB の fields テーブル）。
# 新規デプロイ時に server.py init_db() が count==0 の場合投入する。
# (name, type, area_value, area_unit, spray_rate, spray_volume) — id は AUTOINCREMENT、
# タイムスタンプは投入時に明示指定（fields の DDL に無効なjst defaultはないため）。
# 散布量(spray_volume)=圃場ごとに直接接地（育苗圃=50L/本圃=500L の圃場タイプ既定値）。
FIELDS_SEED = [
    ("育苗圃", "nursery", None, None, None, 50.0),
    ("本圃", "main", None, None, None, 500.0),
]

# 検鏡場所マスターの初期シード（唯一無二の正は DB の inspection_locations テーブル）。
# 花芽分化の顕微鏡検査を行う場所。新規デプロイ時に server.py init_db() が count==0 の場合投入。
INSPECTION_LOCATIONS_SEED = [
    "JA紀の里地域本部",
    "JA紀の里地域本部ふるさとセンター",
]


def seed_from_json(conn):
    """既存のJSONファイルからデータを投入（初回のみ）.
    病害虫は data/diseases.json の廃止により上記 DISEASES_SEED（インライン）から投入."""
    cur = conn.cursor()

    # diseases（インラインシード — 唯一無二の正）
    for d in DISEASES_SEED:
        cur.execute(
            "INSERT OR IGNORE INTO diseases (id, name, type, icon) VALUES (?, ?, ?, ?)",
            d,
        )

    # fields（圃場マスター — インラインシード、count==0 の時だけ）
    now = datetime.utcnow().isoformat()
    n_fields = cur.execute("SELECT COUNT(*) FROM fields").fetchone()[0]
    if n_fields == 0:
        for name, ftype, area_value, area_unit, spray_rate, spray_volume in FIELDS_SEED:
            cur.execute(
                "INSERT INTO fields (name, type, area_value, area_unit, spray_rate, spray_volume, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, ftype, area_value, area_unit, spray_rate, spray_volume, now, now),
            )

    # pesticides.json
    path = os.path.join(APP_ROOT, "data", "pesticides.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            pesticides = json.load(f)
        for p in pesticides:
            cur.execute(
                """INSERT OR IGNORE INTO pesticides
                   (id, name, activeIngredient, category, targetVector, targetNames,
                    phiDays, mixingRestriction, mixingBanTargets, maxApplications,
                    toxicityClass, system, systemCode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    p["id"],
                    p["name"],
                    p.get("activeIngredient"),
                    p.get("category"),
                    json.dumps(p.get("targetVector", [])),
                    json.dumps(p.get("targetNames", [])),
                    p.get("phiDays"),
                    p.get("mixingRestriction"),
                    json.dumps(p.get("mixingBanTargets", [])),
                    p.get("maxApplications"),
                    p.get("toxicityClass"),
                    p.get("system"),
                    p.get("systemCode"),
                ),
            )

    # eval_boxes.json
    path = os.path.join(APP_ROOT, "data", "eval_boxes.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            eval_boxes = json.load(f)
        for eid, eb in eval_boxes.items():
            cur.execute(
                "INSERT OR IGNORE INTO eval_boxes (id, name, vector) VALUES (?, ?, ?)",
                (eid, eb["name"], json.dumps(eb["vector"])),
            )

    # eval_boxes_custom.json
    path = os.path.join(APP_ROOT, "data", "eval_boxes_custom.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            custom = json.load(f)
        for cid, cb in custom.items():
            cur.execute(
                "INSERT OR IGNORE INTO eval_boxes_custom (id, name, vector) VALUES (?, ?, ?)",
                (cid, cb["name"], json.dumps(cb["vector"])),
            )

    conn.commit()


# =============================================================================
# 境界言語の形式知（MODEL）シード — 現状コードの確定値を構造化
# =============================================================================
# 現状コード（rbp-algebra-python/bridges.py の L1〜L6、main.py のスコアリング、
# projection.py の投射）に硬直化していた判断ルールを、形式知として DB に格納する。
# scripts/rbp_engine.py（DB駆動代数）がこれを読み、実行時実装と同一の RBP 行列値を
# 導出する（クローズドループ）。seed_rbp_model は新規DB向け、既存DBは手編集保護。

# 認知軸（entry）の10次元ラベル（diseases テーブルの id 0〜9 と対応）。
_ENTRY_DIMS = [
    "炭疽病", "灰色かび病", "うどんこ病", "ナミハダニ", "ハスモンヨトウ",
    "オオタバコガ", "ミカンキイロアザミウマ", "ワタアブラムシ", "アブラムシ", "コナジラミ",
]

# 認知の正規化仕様（norm_source）: 病害虫キーワード → 次元index の対応表。
# data_loader._derive_target_vector と同一（bit ベクトル生成の法則）。
_ENTRY_NORM_SOURCE = {
    "keyword_map": {
        "炭疽": 0, "灰色かび": 1, "うどんこ": 2, "ハダニ": 3, "ハスモン": 4,
        "オオタバコ": 5, "アザミウマ": 6, "ワタアブラ": 7, "アブラムシ": 8, "コナジラミ": 9,
    }
}

# SPEC-BRIDGE L1〜L6 の述語ベースルール（rule_json）。
# 構造: when（述語AST・DC rbp_engine._eval_when 互換＋STB拡張 op）/
#       action（when 成立時）/ else_action（不成立時）/ penalty（軸, 差分）。
# 変数: target_match / max_applications / usage / interval_days / phi_days /
#       system_code / rotation / mixing_conflict / highly_toxic。
_BRIDGE_RULE_JSON = {
    # L1: ターゲット一致（対象疾患が entry と重ならない → 遮断）
    "SPEC-BRIDGE-TARGET": {
        "when": {"op": ">", "var": "target_match", "const": 0},
        "action": {"type": "full-pass"},
        "else_action": {"type": "full-block"},
        "penalty": None,
    },
    # L2: 散布回数上限（上限到達 → 遮断。-1=無制限）
    "SPEC-BRIDGE-USAGE": {
        "when": {"op": "and", "args": [
            {"op": "!=", "var": "max_applications", "const": -1},
            {"op": ">=", "var": "usage", "const": "max_applications"},
        ]},
        "action": {"type": "full-block"},
        "else_action": {"type": "full-pass"},
        "penalty": None,
    },
    # L3: PHI 残留日（不足 → 減衰0.5・安全ペナルティ）
    "SPEC-BRIDGE-PHI": {
        "when": {"op": "and", "args": [
            {"op": "not-null", "var": "interval_days"},
            {"op": "<", "var": "interval_days", "const": "phi_days"},
        ]},
        "action": {"type": "attenuate", "factor": 0.5},
        "else_action": {"type": "full-pass"},
        "penalty": ["safety", -10.0],
    },
    # L4: 系統ローテーション（同一系統連続2回以上 → 減衰0.3・抵抗性ペナルティ）
    "SPEC-BRIDGE-ROTATION": {
        "when": {"op": "and", "args": [
            {"op": "not-in", "var": "system_code", "values": ["MIX", "PHYSICAL"]},
            {"op": ">=", "var": "rotation", "const": 2},
        ]},
        "action": {"type": "attenuate", "factor": 0.3},
        "else_action": {"type": "full-pass"},
        "penalty": ["resistance", -15.0],
    },
    # L5: 混用可否（前回散布薬剤と混用不可 → 遮断）
    "SPEC-BRIDGE-MIXING": {
        "when": {"op": "truthy", "var": "mixing_conflict"},
        "action": {"type": "full-block"},
        "else_action": {"type": "full-pass"},
        "penalty": None,
    },
    # L6: 毒性区分（劇物 → 減衰0.7・安全ペナルティ）
    "SPEC-BRIDGE-TOXICITY": {
        "when": {"op": "truthy", "var": "highly_toxic"},
        "action": {"type": "attenuate", "factor": 0.7},
        "else_action": {"type": "full-pass"},
        "penalty": ["safety", -8.0],
    },
}

# ブリッジのレベル・名称（level は厳密単調増加・S1）。
_BRIDGES = [
    ("SPEC-BRIDGE-TARGET", 1, "ターゲット一致（対象疾患と entry の重複）"),
    ("SPEC-BRIDGE-USAGE", 2, "散布回数上限"),
    ("SPEC-BRIDGE-PHI", 3, "PHI 残留日"),
    ("SPEC-BRIDGE-ROTATION", 4, "系統ローテーション"),
    ("SPEC-BRIDGE-MIXING", 5, "混用可否"),
    ("SPEC-BRIDGE-TOXICITY", 6, "毒性区分"),
]

# SPEC（候補選択）のスコアリング知識（rbp_spec_condition の rule_json）。
# main.py の _score_set_full の確定値を構造化。
_SPEC_COND_RULE_JSON = {
    "EFFECTIVENESS": {"mirror_weight": 10, "coverage_weight": 5},
    "SAFETY": {"base": 20, "floor": 0, "penalty_axis": "safety"},
    "RESISTANCE": {"base": 15, "floor": 0, "penalty_axis": "resistance",
                   "combo_adjustment": -20,
                   "non_rotation_codes": ["MIX", "PHYSICAL"]},
    "SELECTION": {"metric": "cosine", "set_sizes": [1, 2]},
    "TIEBREAK": {"order": ["mirror_id:desc", "total_score:desc",
                           "set_size:asc", "id:asc"]},
}

# 投射テンプレート（非実行・ドキュメント用参照物。実際は projection.py が唯一の正）。
_PROJECTION_TEMPLATES = {
    "diagnostic": (
        "【{eval_box_name}】\n"
        "今回の防除の薬剤は、{drug_names}、です。\n\n"
        "【スコア内訳】\n"
        "ミラーID: {mirror_id:.2f}\n"
        "有効性スコア: {effectiveness:.1f}\n"
        "  有効性: ミラーID={mirror_id:.2f}, カバレッジ={coverage:.0%} ({match_count}/{target_sum})\n"
        "  安全性: {safety:.1f}\n"
        "  抵抗性: {resistance:.1f}\n\n"
        "【ブリッジ通過履歴（全候補）】\n"
        "{bridge_trace}\n\n"
        "【代替案】\n{alternatives}\n\n"
        "【除外された薬剤】\n{excluded}"
    ),
    "actuation-token": (
        '{"vector": {vector}, "eval_box_id": "{eval_box_id}"}'
    ),
    "cron-text": (
        "📅 {schedule_date} 防除予定（{set_label}）\n"
        "🐛 {notes}\n"
        "💊 処方: {prescription}\n"
        "🪞 ミラーID {mirror_id:.4f}\n"
        "📊 スコア {total_score} / 代替 {alt_count}案"
    ),
}


def seed_rbp_model(conn):
    """境界言語の形式知（MODEL/ACTUATION）を DB 化（現状コードの確定値）。

    重複時はスキップ（冪等）。data/eval_boxes.json を読み 22 BOX を投入。
    """
    counts = {}
    D = "STB-PEST"

    # rbp_domain
    if not conn.execute("SELECT 1 FROM rbp_domain WHERE domain_id=?", (D,)).fetchone():
        conn.execute(
            "INSERT INTO rbp_domain "
            "(domain_id, name, layer_specbridge, fold_order, spec_kind, "
            " candidate_pool, candidate_pool_label) VALUES (?,?,?,?,?,?,?)",
            (D, "病害虫防除", "present", "level-ascending",
             "candidate-selection", "pesticides", "薬剤候補"))
        counts["rbp_domain"] = 1
    else:
        counts["rbp_domain"] = 0

    # rbp_perception_axis（entry 単一軸・10次元 bit・全0=fail）
    if not conn.execute(
            "SELECT 1 FROM rbp_perception_axis WHERE domain_id=? AND axis_name=?",
            (D, "entry")).fetchone():
        conn.execute(
            "INSERT INTO rbp_perception_axis "
            "(domain_id, axis_name, seq, dims, dim_type, norm_source, norm_basis, "
            " check_all_zero, lead, threshold) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (D, "entry", 1, json.dumps(_ENTRY_DIMS, ensure_ascii=False), "bit",
             json.dumps(_ENTRY_NORM_SOURCE, ensure_ascii=False), "disease-keyword",
             "fail", None, None))
        counts["rbp_perception_axis"] = 1
    else:
        counts["rbp_perception_axis"] = 0

    # rbp_eval_box（完全一致。data/eval_boxes.json ＋ 自動登録の
    # eval_boxes_custom.json。実行時 load_eval_boxes と同一の BOX セット）。
    inserted = 0
    for boxes_path in ("eval_boxes.json", "eval_boxes_custom.json"):
        p = os.path.join(APP_ROOT, "data", boxes_path)
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            boxes = json.load(f)
        for box_id, entry in boxes.items():
            if not conn.execute(
                    "SELECT 1 FROM rbp_eval_box WHERE domain_id=? AND box_id=?",
                    (D, box_id)).fetchone():
                conn.execute(
                    "INSERT INTO rbp_eval_box (domain_id, box_id, box_name, condition, threshold, members) "
                    "VALUES (?,?,?,?,?,?)",
                    (D, box_id, entry["name"], "exact", None,
                     json.dumps(entry["vector"], ensure_ascii=False)))
                inserted += 1
    counts["rbp_eval_box"] = inserted

    # rbp_bridge（L1〜L6・述語ベース rule_json）
    inserted = 0
    for bid, lvl, _desc in _BRIDGES:
        if not conn.execute(
                "SELECT 1 FROM rbp_bridge WHERE domain_id=? AND bridge_id=?",
                (D, bid)).fetchone():
            conn.execute(
                "INSERT INTO rbp_bridge (domain_id, bridge_id, level, dim, open_at, rule_json) "
                "VALUES (?,?,?,?,?,?)",
                (D, bid, lvl, None, None,
                 json.dumps(_BRIDGE_RULE_JSON[bid], ensure_ascii=False)))
            inserted += 1
    counts["rbp_bridge"] = inserted

    # rbp_spec_condition（スコアリング知識）
    conds = [
        ("EFFECTIVENESS", "variable", "engine", "main._score_set_full",
         "有効性スコア = Mirror-ID × 10 + カバレッジ率 × 5",
         _SPEC_COND_RULE_JSON["EFFECTIVENESS"], 1),
        ("SAFETY", "variable", "engine", "main._score_set_full",
         "安全性スコア = max(0, 20 + 安全ペナルティ合計)",
         _SPEC_COND_RULE_JSON["SAFETY"], 2),
        ("RESISTANCE", "variable", "engine", "main._score_set_full",
         "抵抗性スコア = max(0, 15 + 抵抗性ペナルティ合計 + 組み合わせ調整(同一系統2剤=-20))",
         _SPEC_COND_RULE_JSON["RESISTANCE"], 3),
        ("SELECTION", "variable", "engine", "main.build_prescription",
         "候補選択: Mirror-ID（cosine類似度）で候補セットを評価、1剤・2剤セットを列挙",
         _SPEC_COND_RULE_JSON["SELECTION"], 4),
        ("TIEBREAK", "fixed", "engine", "main.build_prescription",
         "タイブレーク: mirrorId降順 → totalScore降順 → セットサイズ昇順 → id昇順",
         _SPEC_COND_RULE_JSON["TIEBREAK"], 5),
    ]
    inserted = 0
    for cname, kind, source, sref, rule, rule_json, seq in conds:
        if not conn.execute(
                "SELECT 1 FROM rbp_spec_condition WHERE domain_id=? AND cond_name=?",
                (D, cname)).fetchone():
            conn.execute(
                "INSERT INTO rbp_spec_condition "
                "(domain_id, cond_name, kind, source, source_ref, decision_rule, rule_json, seq) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (D, cname, kind, source, sref, rule,
                 json.dumps(rule_json, ensure_ascii=False), seq))
            inserted += 1
    counts["rbp_spec_condition"] = inserted

    # rbp_spec_gate（発火ゲート: 認知OK ∧ BOX到達）
    if not conn.execute("SELECT 1 FROM rbp_spec_gate WHERE domain_id=?", (D,)).fetchone():
        conn.execute(
            "INSERT INTO rbp_spec_gate (domain_id, pre, open_expr) VALUES (?,?,?)",
            (D, "perception-ok", "box-reached"))
        counts["rbp_spec_gate"] = 1
    else:
        counts["rbp_spec_gate"] = 0

    # rbp_projection（投射テンプレート・非実行参照物）
    inserted = 0
    for output_name, template in _PROJECTION_TEMPLATES.items():
        if not conn.execute(
                "SELECT 1 FROM rbp_projection WHERE domain_id=? AND output_name=?",
                (D, output_name)).fetchone():
            conn.execute(
                "INSERT INTO rbp_projection (domain_id, output_name, mode, template) VALUES (?,?,?,?)",
                (D, output_name, "pure-mapping", template))
            inserted += 1
    counts["rbp_projection"] = inserted

    # actuation_channels（作動チャンネル。秘密情報は .env 参照のみ）
    if not conn.execute(
            "SELECT 1 FROM actuation_channels WHERE channel_key=?", ("slack-stb",)).fetchone():
        conn.execute(
            "INSERT INTO actuation_channels (channel_key, channel_type, target, config) VALUES (?,?,?,?)",
            ("slack-stb", "slack", "防除暦チャンネル",
             json.dumps({"webhook_env": "SLACK_WEBHOOK_URL"}, ensure_ascii=False)))
        counts["actuation_channels"] = 1
    else:
        counts["actuation_channels"] = 0

    # actuation_rules（作動ルール: 処方発火 → Slack 通知）
    if not conn.execute(
            "SELECT 1 FROM actuation_rules WHERE domain_id=? AND trigger=? AND channel_key=?",
            (D, "fire", "slack-stb")).fetchone():
        conn.execute(
            "INSERT INTO actuation_rules (domain_id, trigger, actor, actor_type, channel_key, payload) "
            "VALUES (?,?,?,?,?,?)",
            (D, "fire", "stb-operator", "employee", "slack-stb", "cron-text"))
        counts["actuation_rules"] = 1
    else:
        counts["actuation_rules"] = 0

    conn.commit()
    return counts


def main():
    os.makedirs(os.path.join(APP_ROOT, "data"), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(CREATE_SQL)
    # 既存DBへの field_id 追加（fields テーブル作成後）
    migrate_add_field_id(conn)
    seed_from_json(conn)
    # 境界言語の形式知（MODEL/ACTUATION）を DB 化（重複時はスキップ）
    seed_rbp_model(conn)

    # Counts
    for table in ("pesticides", "diseases", "fields", "eval_boxes",
                  "eval_boxes_custom", "spray_history"):
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"  {table}: {count} rows")

    conn.close()
    print(f"\nDB created: {DB_PATH}")


if __name__ == "__main__":
    main()
