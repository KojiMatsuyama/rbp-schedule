#!/usr/bin/env python3
# server.py — STBアプリ用の静的ファイル配信 + SQLite永続化サーバー。
# IP/ポート/バインド/キャッシュ無効化/ThreadingHTTPServer必須
#
# SQLite (data/stb.db) に以下のテーブルを持つ:
#   pesticides, diseases, eval_boxes, eval_boxes_custom, spray_history, spray_schedule, inventory
#
# 要求評価RBP（rbp/eval_box_registry.js）が自動登録した新しいEVAL_BOXを
# eval_boxes_custom テーブルへ永続化する。
import glob
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

# 発火経路（認知ゲート等）のログ出力先。nohup/背景起動で stderr → server.log へ。
logger = logging.getLogger("stb.server")

# 病害虫の唯一無一の正（SQLite diseases テーブル）の bootstrap シード元。
# 圃場マスターの bootstrap シード元も db_setup.py に一元化。
from db_setup import DISEASES_SEED, FIELDS_SEED, INSPECTION_LOCATIONS_SEED, migrate_add_field_id, seed_rbp_model


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_activate(self):
        self.socket.listen(128)

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_ROOT)

DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
VECTOR_DIM = 10

# 事業主体（三果倉）＝ 4-level ID の subject_id ＝ net_id。
# 要求の発生源・接地の根（ts/真界_事業主体設計.md §2・§4）。humos.py の _NET_ID と同一。
_SUBJECT_ID = "mikakura"
_SUBJECT_NAME = "三果倉"
_DOMAIN_ID = "iichigo"   # 事業ドメイン（いちご）＝ 4-level ID の domain_id

# ── 境界言語の形式知（rbp_*）CRUD のテーブル定義（🧩 知識メンテ用）────────
# 各テーブルの主キー（pk）・自動採番（auto）・更新可能カラムを宣言する。
# JSON 文字列列（members / rule_json / dims / norm_source 等）は文字列のまま
# 保存する（UI 側で文字列↔オブジェクトの変換を行う）。
_RBP_TABLES = {
    "rbp_domain": {
        "pk": "domain_id", "auto": False,
        "columns": ["name", "layer_specbridge", "fold_order"],
    },
    # タスク（業務）単位。task_id は手動主キー（auto=False）。
    "rbp_task": {
        "pk": "task_id", "auto": False,
        "columns": ["domain_id", "name", "description"],
    },
    # 作動（=T の正体・物理界的作動）。
    "rbp_actuation": {
        "pk": "id", "auto": True,
        "columns": ["task_id", "name", "handler", "description"],
    },
    "rbp_perception_axis": {
        "pk": "id", "auto": True,
        "columns": ["domain_id", "task_id", "axis_name", "seq", "dims", "dim_type",
                    "norm_source", "norm_basis", "check_all_zero", "lead", "threshold"],
    },
    "rbp_eval_box": {
        "pk": "id", "auto": True,
        "columns": ["domain_id", "task_id", "box_id", "box_name", "condition", "threshold", "members"],
    },
    "rbp_bridge": {
        "pk": "id", "auto": True,
        "columns": ["domain_id", "task_id", "bridge_id", "level", "dim", "open_at", "rule_json"],
    },
    "rbp_spec_condition": {
        "pk": "id", "auto": True,
        "columns": ["domain_id", "task_id", "cond_name", "kind", "source", "source_ref",
                    "decision_rule", "rule_json", "seq"],
    },
    "rbp_spec_gate": {
        "pk": "domain_id", "auto": False,
        "columns": ["task_id", "pre", "open_expr"],
    },
    "rbp_projection": {
        "pk": "id", "auto": True,
        "columns": ["domain_id", "task_id", "output_name", "mode", "template"],
    },
}

# --- SQLite helpers ---

def get_db():
    """Return a thread-local SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _assignee_ids_from_row(row):
    """行の assignee_ids（JSON 配列文字列）を整数リストにパースする。

    未設定（NULL/空）なら []。不正な JSON は [] にフォールバックする。
    """
    raw = row["assignee_ids"] if "assignee_ids" in row.keys() else None
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [int(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _assignee_ids_from_body(body):
    """リクエストボディから assignee_ids（整数リスト）を取り出す。

    単一の assignee_id も併せて許容し、assignee_ids が無い場合は
    [assignee_id]（単一）として扱う（後方互換・旧クライアント）。
    """
    v = body.get("assignee_ids")
    if v is not None:
        try:
            return [int(x) for x in v] if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []
    single = body.get("assignee_id")
    return [int(single)] if single is not None else []


def _migrate_rbp_domain_spec_kind(conn):
    """既存DBの rbp_domain に <Spec-Profile> 列（spec_kind / candidate_pool*）を追加。

    CREATE TABLE IF NOT EXISTS では既存テーブルに列が追えないため、PRAGMA
    table_info で確認し無ければ ALTER で追加する（idempotent）。STB は
    candidate-selection（候補選択）なので、既存行が condition-decision の
    既定値のままなら、候補プールの指針（pesticides / 薬剤候補）を補う。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rbp_domain)")}
    if "spec_kind" not in cols:
        conn.execute("ALTER TABLE rbp_domain "
                     "ADD COLUMN spec_kind TEXT NOT NULL DEFAULT 'condition-decision'")
    if "candidate_pool" not in cols:
        conn.execute("ALTER TABLE rbp_domain ADD COLUMN candidate_pool TEXT")
    if "candidate_pool_label" not in cols:
        conn.execute("ALTER TABLE rbp_domain ADD COLUMN candidate_pool_label TEXT")
    # 既存行（列追加で condition-decision 既定値のまま）を STB の正に補う。
    # 注意: init_db の接続は row_factory 未設定（tuple 返り）なので位置アクセス。
    row = conn.execute(
        "SELECT domain_id, spec_kind FROM rbp_domain WHERE domain_id='STB-PEST'"
    ).fetchone()
    if row and row[1] != "candidate-selection":
        conn.execute(
            "UPDATE rbp_domain SET spec_kind='candidate-selection', "
            "candidate_pool='pesticides', candidate_pool_label='薬剤候補' "
            "WHERE domain_id='STB-PEST' AND spec_kind='condition-decision'"
        )
    conn.commit()


def _migrate_pesticide_product_master(conn):
    """pesticides を「薬剤商品マスター」に昇格 — 商品仕様の列追加（idempotent）。

    技術仕様（RBP 用）は揃っているが、**買う・使うために必要な商品仕様**が
    抜けていた。決定（何を何個買うか）を一意にするには容量（capacity+packUnit）と
    販売単位（packSize）、梱包形態（packaging）、投射（いくらか）には散布量
    （applicationRate）が必要。

    列追加は idempotent（PRAGMA table_info で確認し無ければ ALTER）。
    値（67 薬剤の販売単位・剤形・散布量・価格）は緒言（人間登録）で埋める。
    P01（ベルクート）のみ inventory に商品データがある（ベルクートWP・g・住園化学）
    ため、brand/formulation/packUnit を seed する（値が未登録の行のみ）。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pesticides)")}
    added = False
    for col, decl in [
        ("brand", "TEXT"),
        ("formulation", "TEXT"),
        ("packaging", "TEXT"),
        ("contentForm", "TEXT"),
        ("applicationRate", "REAL"),
        ("capacity", "REAL"),
        ("packSize", "REAL"),
        ("packUnit", "TEXT"),
        ("price", "REAL"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE pesticides ADD COLUMN {col} {decl}")
            added = True
    if added:
        conn.commit()
        print("  migrate: ALTER TABLE pesticides ADD COLUMN (商品マスター)")
    # P01（ベルクート）を inventory の商品データから seed（値が未登録の行のみ）。
    # inventory: productName=ベルクートWP, unit=g, supplier=住園化学。
    # 剤形は productName の接尾辞（WP/EC/SC/GR…）から抽出。
    inv = conn.execute(
        "SELECT productName, unit, supplier FROM inventory WHERE pesticideId='P01' LIMIT 1"
    ).fetchone()
    if inv:
        product_name, unit, supplier = inv[0], inv[1], inv[2]
        # 剤形 = productName の末尾英字シークエンス（ベルクートWP → WP）
        formulation = None
        if product_name:
            tail = product_name.rstrip()
            eng = ""
            for ch in reversed(tail):
                if ch.isascii() and ch.isalpha():
                    eng = ch + eng
                else:
                    break
            if eng:
                formulation = eng.upper()
        conn.execute(
            "UPDATE pesticides SET "
            "brand = COALESCE(brand, ?), "
            "formulation = COALESCE(formulation, ?), "
            "packUnit = COALESCE(packUnit, ?) "
            "WHERE id='P01'",
            (supplier, formulation, unit),
        )
        conn.commit()
        print(f"  seed: pesticides P01 商品データ（brand={supplier}, "
              f"formulation={formulation}, packUnit={unit}）")


def _migrate_field_spray(conn):
    """fields に散布属性（spray_rate / spray_volume）を追加 — idempotent。

    圃場ドメインオブジェクトの散布属性拡張。散布量は圃場レベルの接地属性として
    置き、防除要求イベントが要求トークンに付与する（オブジェクト接地 §4.1）。

    - spray_volume (L) = 散布量。**圃場ごとに直接接地**（緒言・圃場オブジェクト属性）。
                         防除の仕様決定が 必要量 = spray_volume を消費する。
    - spray_rate (L/10a) = 散布率（任意・参考）。圃場ごとに直接接地する方式では
                         導出の源にしない（圃場ごとに散布量が独立）。

    圃場タイプ別の散布量既定値（育苗圃=50L / 本圃=500L）を、値が未登録の圃場に
    接地する（圃場ごとに個別編集可能）。現行実務「本圃=500L / 育苗圃=50L」の
    圃場タイプ固定を、圃場オブジェクトの接地属性として表現する。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fields)")}
    added = False
    for col, decl in [("spray_rate", "REAL"), ("spray_volume", "REAL")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE fields ADD COLUMN {col} {decl}")
            added = True
    if added:
        conn.commit()
        print("  migrate: ALTER TABLE fields ADD COLUMN (散布属性)")
    # 圃場タイプ別の散布量既定値（育苗圃=50L / 本圃=500L）。
    # 圃場ごとに直接接地（値が未登録の圃場のみ・個別編集可能）。
    for ftype, vol in (("nursery", 50.0), ("main", 500.0)):
        conn.execute(
            "UPDATE fields SET spray_volume=?, updated_at=? "
            "WHERE type=? AND (spray_volume IS NULL)",
            (vol, datetime.datetime.utcnow().isoformat(), ftype),
        )
    conn.commit()


# 知識を持つタスク（業務）単位で task_id を付与する rbp_* テーブル。
# 普遍構造 T = {認知,評価,決定,投射} + 作動 に合わせ、タスクの知識=5段の行集合。
_RBP_TASK_TABLES = (
    "rbp_perception_axis",   # 認知
    "rbp_eval_box",          # 評価
    "rbp_bridge",            # 決定（ブリッジ）
    "rbp_spec_condition",    # 決定（仕様条件）
    "rbp_spec_gate",         # 決定（発火ゲート）
    "rbp_projection",        # 投射
)


def _migrate_rbp_task(conn):
    """既存DBの rbp_* に task_id（タスク=業務単位）を追加 + rbp_task/rbp_actuation 新設。

    普遍構造（T = {認知,評価,決定,投射} + 作動）に合わせ、知識を**タスク単位**で持つ。
    現状は rbp_domain が 1 件（STB-PEST=薬剤選定）で、認知軸・BOX・仕様条件・投射がその
    1 ドメインに平らに一覧されていた（タスク単位のキーなし）。task_id を各 rbp_* に追加し、
    タスクの知識=5段の行集合にする。既存行は task_id='rx-select'（薬剤選定）にバックフィル
    （後方互換: nullable 追加・DC も rbp_* を使うが task_id 未設定行は NULL のまま）。
    """
    # 1. rbp_task（タスク登録: タスク=業務単位）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rbp_task (
            task_id     TEXT PRIMARY KEY,
            domain_id   TEXT REFERENCES rbp_domain(domain_id),
            name        TEXT NOT NULL,
            description TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    # 2. rbp_actuation（作動=Tの正体。物理界的作動）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rbp_actuation (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     TEXT NOT NULL REFERENCES rbp_task(task_id),
            name        TEXT NOT NULL,
            handler     TEXT,
            description TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    # 3. 各 rbp_* に task_id を追加（idempotent・nullable=後方互換）
    for t in _RBP_TASK_TABLES:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        if "task_id" not in cols:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN task_id TEXT")
    # 4. 既存行を task_id='rx-select'（薬剤選定）にバックフィル
    for t in _RBP_TASK_TABLES:
        conn.execute(f"UPDATE {t} SET task_id='rx-select' WHERE task_id IS NULL")
    # 5. タスク登録（薬剤選定）+ 作動（Slack送信処方=Tの正体）
    conn.execute(
        "INSERT OR IGNORE INTO rbp_task (task_id, domain_id, name, description) "
        "VALUES ('rx-select', 'STB-PEST', '薬剤選定', '要求評価→仕様決定→薬剤選定（RBP行列）')"
    )
    conn.execute(
        "INSERT INTO rbp_actuation (task_id, name, handler, description) "
        "SELECT 'rx-select', 'Slack送信（処方）', 'sos.slack.send_message', "
        "'処方をSlack送信（作動=Tの正体）' "
        "WHERE NOT EXISTS (SELECT 1 FROM rbp_actuation WHERE task_id='rx-select')"
    )
    # 6. コホート（定植）業務のタスク登録。コホートネットの各業務 T が task_id を持ち、
    #    /knowledge?task=<task_id> で**タスク単位**にナレッジを編集する。最初は写生
    #    （5段未実装=0/5）で、ユーザーが逐次知識を埋めていく（漸増的実装・§12）。
    _cohort_tasks = [
        ("inspection",  "検鏡",         "花芽分化確認（顕微鏡検査・外部組織判定）"),
        ("prep",        "薬剤準備",     "定植前の薬剤準備"),
        ("gloves",      "手袋",         "手袋の揃い"),
        ("mulch",       "マルチ敷設",   "定植前のマルチ敷設"),
        ("clear-mulch", "透明マルチ敷設", "熱消毒前の透明マルチ敷設"),
        ("irrigation",  "潅水調整",     "熱消毒前の潅水調整"),
        ("heat",        "熱消毒",       "ベッド準備（熱消毒・3週間以上実施）"),
        ("transplant",  "定植",         "育苗圃苗の定植（コホートの終端）"),
    ]
    for tid, name, desc in _cohort_tasks:
        conn.execute(
            "INSERT OR IGNORE INTO rbp_task (task_id, domain_id, name, description) "
            "VALUES (?, 'STB-PEST', ?, ?)", (tid, name, desc)
        )
    # 7. マルチ敷設（task_id='mulch'）の RBP プログラムをシード（§12.7 ナレッジ設計方法論）。
    #    「どんな単純でも構造は統一」: 薬剤選定（rx-select）と同型の完全な RBP プログラム
    #    （認知軸=要求ベクトル / 評価BOX=要求評価RBP行列(members) / ブリッジ=仕様決定RBP行列 /
    #    ゲート / 仕様条件=ミラーIDスコアリング / 投射=手順書 / 作動=Slack）を task_id='mulch'
    #    で登録する。各トランジション（タスク）が自己完結した RBP プログラムを持つ。
    #    要求ベクトル = 圃場の beds が要求するマルチ種別の one-hot（3次元: 白黒/透明/黒）。
    #    資材候補（mulch_materials）の target_vector = 資材の type の one-hot（同法則）。
    #    人間が登録するのは緒言DB（beds・mulch_materials・mulch_inventory）への指針、
    #    RBP 行列は境界言語で自動導出（derive_bnf.py --task mulch / rbp_engine.py --task mulch）。
    _mulch_domain = "STB-PEST"
    _mulch_dims = json.dumps(["白黒", "透明", "黒"], ensure_ascii=False)
    _mulch_norm = json.dumps({"keyword_map": {"白黒": 0, "透明": 1, "黒": 2}}, ensure_ascii=False)
    # 認知: マルチ敷設要求の軸 = マルチ種別の 3 次元 bit ベクトル（要求ベクトル）。
    #    ベッド台帳（beds）が要求する種別を one-hot でベクトル化する軸。
    #    旧シード（dims='beds'・real）は構造統一前の写生行 → 新構造に UPDATE（冪等）。
    conn.execute(
        "UPDATE rbp_perception_axis SET dims=?, dim_type='bit', norm_source=?, "
        "norm_basis='ベッド台帳（beds）が要求するマルチ種別を one-hot でベクトル化' "
        "WHERE task_id='mulch'",
        (_mulch_dims, _mulch_norm)
    )
    conn.execute(
        "INSERT INTO rbp_perception_axis "
        "(domain_id, task_id, axis_name, seq, dims, dim_type, norm_source, norm_basis, check_all_zero, lead, threshold) "
        "SELECT ?, 'mulch', 'マルチ敷設要求', 1, ?, 'bit', ?, "
        "'ベッド台帳（beds）が要求するマルチ種別を one-hot でベクトル化', 'fail', NULL, NULL "
        "WHERE NOT EXISTS (SELECT 1 FROM rbp_perception_axis WHERE task_id='mulch')",
        (_mulch_domain, _mulch_dims, _mulch_norm)
    )
    # 評価: 要求評価BOX = **マルチ選定グループ**（context × mulch_type）。
    #    要求評価は「(context × mulch_type) のグループ」で要求を分類し、グループの
    #    参照ベクトル（members=type の one-hot）と要求ベクトルのコサイン類似度=
    #    **ミラーID** を付与する（ユーザー指定 2026-09-07: 「定植白黒マルチグループが
    #    選択される、ミラーID」）。context=要求の文脈（定植/熱消毒）。
    #    members 付き exact BOX（derive_bnf.py の S5 充足）。
    _mulch_boxes = [
        ("MULCH-01", "定植白黒マルチグループ", "定植", [1, 0, 0]),
        ("MULCH-02", "熱消毒透明マルチグループ", "熱消毒", [0, 1, 0]),
        ("MULCH-03", "定植白黒+黒マルチグループ", "定植", [1, 0, 1]),
        ("MULCH-04", "定植黒マルチグループ", "定植", [0, 0, 1]),
    ]
    for _box_id, _box_name, _box_ctx, _members in _mulch_boxes:
        conn.execute(
            "INSERT INTO rbp_eval_box (domain_id, task_id, box_id, box_name, condition, threshold, members, context) "
            "SELECT ?, 'mulch', ?, ?, 'exact', NULL, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM rbp_eval_box WHERE task_id='mulch' AND box_id=?)",
            (_mulch_domain, _box_id, _box_name, json.dumps(_members), _box_ctx, _box_id)
        )
        # 既存行（旧シード: box_name=種別名・context=NULL）をグループ構造に更新（冪等）。
        conn.execute(
            "UPDATE rbp_eval_box SET box_name=?, context=?, members=? "
            "WHERE task_id='mulch' AND box_id=?",
            (_box_name, _box_ctx, json.dumps(_members), _box_id)
        )
    # 決定: ブリッジ = 仕様決定RBP行列（候補フィルタ・述語ベース rule_json）。
    #    L1 TARGET: 資材が要求種別をカバーするか（target_match>0）。
    #    L2 STOCK : 資材に在庫があるか（mulch_inventory.quantity>0）。
    _mulch_bridges = [
        ("MULCH-BRIDGE-TARGET", 1,
         {"when": {"op": ">", "var": "target_match", "const": 0},
          "action": {"type": "full-pass"}, "else_action": {"type": "full-block"}, "penalty": None}),
        ("MULCH-BRIDGE-STOCK", 2,
         {"when": {"op": ">", "var": "stock", "const": 0},
          "action": {"type": "full-pass"}, "else_action": {"type": "full-block"}, "penalty": None}),
    ]
    for _bid, _lvl, _rule in _mulch_bridges:
        conn.execute(
            "INSERT INTO rbp_bridge (domain_id, task_id, bridge_id, level, dim, open_at, rule_json) "
            "SELECT ?, 'mulch', ?, ?, NULL, NULL, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM rbp_bridge WHERE task_id='mulch' AND bridge_id=?)",
            (_mulch_domain, _bid, _lvl, json.dumps(_rule, ensure_ascii=False), _bid)
        )
    # 決定: 発火ゲート（認知OK ∧ BOX到達）はドメイン共通の不変条件（rbp_spec_gate の
    #    PK=domain_id・エンジンも WHERE domain_id=? で1つ読む）。既存の STB-PEST ゲートを
    #    共有するため、タスク毎のゲートは登録しない。決定段の接地は仕様条件5＋ブリッジ2で充足。
    # 決定: 仕様条件 = ミラーIDスコアリング（rx-select と同型の 5 条件）。
    #    マルチでは SAFETY/RESISTANCE はペナルティ軸を持たないため base スコアのまま
    #    （構造は統一・値はタスク固有）。SELECTION=候補選択（cosine・1資材セット）。
    _mulch_conds = [
        ("EFFECTIVENESS", "variable", "engine", "mulch._score_set",
         "有効性スコア = ミラーID × 10 + カバレッジ率 × 5",
         {"mirror_weight": 10, "coverage_weight": 5}, 1),
        ("SAFETY", "variable", "engine", "mulch._score_set",
         "安全性スコア = base（マルチには安全ペナルティ軸なし）",
         {"base": 20, "floor": 0, "penalty_axis": "safety"}, 2),
        ("RESISTANCE", "variable", "engine", "mulch._score_set",
         "抵抗性スコア = base（マルチには抵抗性ペナルティ軸なし）",
         {"base": 15, "floor": 0, "penalty_axis": "resistance"}, 3),
        ("SELECTION", "variable", "mulch_materials", "beds.mulch_type",
         "候補選択: ミラーID（cosine類似度）で資材候補を評価・1資材セット",
         {"metric": "cosine", "set_sizes": [1]}, 4),
        ("TIEBREAK", "fixed", "engine", "mulch.build_prescription",
         "タイブレーク: ミラーID降順 → 総合スコア降順 → セットサイズ昇順 → id昇順",
         {"order": ["mirror_id:desc", "total_score:desc", "set_size:asc", "id:asc"]}, 5),
    ]
    for _cond_name, _kind, _src, _ref, _rule, _rule_json, _seq in _mulch_conds:
        conn.execute(
            "INSERT INTO rbp_spec_condition "
            "(domain_id, task_id, cond_name, kind, source, source_ref, decision_rule, rule_json, seq) "
            "SELECT ?, 'mulch', ?, ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM rbp_spec_condition WHERE task_id='mulch' AND cond_name=?)",
            (_mulch_domain, _cond_name, _kind, _src, _ref, _rule,
             json.dumps(_rule_json, ensure_ascii=False), _seq, _cond_name)
        )
    # 旧プレースホルダ行のクリーンアップ（構造統一前の写生行・S5 違反の原因）。
    #   - 評価BOX 'mulch-req'（members なし exact BOX）→ S5 違反で STB-PEST 導出を壊していた。
    #     新構造の MULCH-01〜04（members 付き）に置換済み。
    #   - 仕様条件 'mulch_material'（変数抽出のみ・rule_json なし）→ 新構造の 5 条件に置換済み。
    conn.execute(
        "DELETE FROM rbp_eval_box WHERE task_id='mulch' AND box_id='mulch-req'")
    conn.execute(
        "DELETE FROM rbp_spec_condition WHERE task_id='mulch' AND cond_name='mulch_material'")
    # 投射: 手順書テンプレート（$[本舗] / $[白黒マルチ] 変数を仕様決定で抽出した値と合成）。
    # mode=pure-mapping（非実行参照物・人間が読む手順書）。
    _mulch_proc_template = (
        "$[本舗]：$[白黒マルチ]の黒を表にして、マルチのはさみで穴をあけて、"
        "ベッドの北側から潅水の空気抜きに引っ掛け、ベッドの端まで転がすように敷設していく。"
        "そこで、はさみでカットして、南の端と北のはしをベッドの端を包み込むように縛る。"
    )
    conn.execute(
        "INSERT INTO rbp_projection (domain_id, task_id, output_name, mode, template) "
        "SELECT ?, 'mulch', '敷設手順書', 'pure-mapping', ? "
        "WHERE NOT EXISTS (SELECT 1 FROM rbp_projection WHERE task_id='mulch')",
        (_mulch_domain, _mulch_proc_template)
    )
    # 作動: マルチ敷設通知（Slack）= T の正体（物理界的作動）。
    conn.execute(
        "INSERT INTO rbp_actuation (task_id, name, handler, description) "
        "SELECT 'mulch', '敷設通知（Slack）', 'sos.slack.send_message', "
        "'敷設手順書を合成してSlack通知（作動=Tの正体）' "
        "WHERE NOT EXISTS (SELECT 1 FROM rbp_actuation WHERE task_id='mulch')"
    )
    conn.commit()


def _migrate_rbp_spec_condition_unique(conn):
    """rbp_spec_condition の UNIQUE を (domain_id, cond_name) → (domain_id, cond_name, task_id) に再構築。

    各トランジション（タスク）が自己完結した RBP プログラムを持つため、同じ
    domain（STB-PEST）内で rx-select と mulch が同名の仕様条件（EFFECTIVENESS /
    SAFETY / RESISTANCE / SELECTION / TIEBREAK）を持つ。task_id を UNIQUE に含めず
    同名条件が衝突するため再構築する（SQLite は UNIQUE 制約の追加/変更ができないため
    テーブル再構築）。idempotent（既存の UNIQUE が既に task_id を含めれば何もしない）。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='rbp_spec_condition'"
    ).fetchone()
    if not row:
        return
    # 既に (domain_id, cond_name, task_id) で UNIQUE なら何もしない。
    # （init_db 内は row_factory 未設定 → row は tuple・sql は row[0]）
    if "UNIQUE(domain_id,cond_name,task_id)" in row[0].replace(" ", ""):
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with conn:
            conn.execute("ALTER TABLE rbp_spec_condition RENAME TO rbp_spec_condition_old")
            conn.execute("""
                CREATE TABLE rbp_spec_condition (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain_id     TEXT NOT NULL REFERENCES rbp_domain(domain_id),
                    cond_name     TEXT NOT NULL,
                    kind          TEXT NOT NULL CHECK(kind IN ('variable','fixed')),
                    source        TEXT,
                    source_ref    TEXT,
                    decision_rule TEXT,
                    rule_json     TEXT,
                    seq           INTEGER NOT NULL,
                    task_id       TEXT,
                    UNIQUE(domain_id, cond_name, task_id)
                )
            """)
            conn.execute("""
                INSERT INTO rbp_spec_condition
                    (id, domain_id, cond_name, kind, source, source_ref,
                     decision_rule, rule_json, seq, task_id)
                SELECT id, domain_id, cond_name, kind, source, source_ref,
                       decision_rule, rule_json, seq, task_id
                FROM rbp_spec_condition_old
            """)
            conn.execute("DROP TABLE rbp_spec_condition_old")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pesticides (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            activeIngredient TEXT,
            category TEXT,
            targetVector TEXT,
            targetNames TEXT,
            phiDays REAL,
            mixingRestriction TEXT,
            mixingBanTargets TEXT,
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
            vector TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eval_boxes_custom (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            vector TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spray_history (
            date TEXT PRIMARY KEY,
            pests TEXT NOT NULL,
            vector TEXT NOT NULL,
            field_id INTEGER REFERENCES fields(id)
        );

        CREATE TABLE IF NOT EXISTS spray_schedule (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_date   TEXT    NOT NULL,
            actual_date     TEXT,
            status          TEXT    NOT NULL DEFAULT 'scheduled'
                        CHECK(status IN ('scheduled', 'done', 'missed', 'rescheduled')),
            trigger_type    TEXT    NOT NULL DEFAULT 'cycle'
                        CHECK(trigger_type IN ('cycle', 'observation', 'forecast')),
            trigger_ref     TEXT,
            eval_box_id     TEXT REFERENCES eval_boxes(id),
            rb_out_json     TEXT,
            set_ids         TEXT    NOT NULL,
            pesticide_ids   TEXT    NOT NULL,
            operator        TEXT,
            assignee_ids    TEXT,                      -- 散布担当（従業者IDのJSON配列・複数選択可）
            weather         TEXT,
            notes           TEXT,
            field_id        INTEGER REFERENCES fields(id),
            created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst'))
        );

        -- 圃場マスター（育苗／本圃）。面積は数値+単位。
        -- タイムスタンプは投入側が明示指定（'jst' は無効な修飾子のためdefaultにしない）。
        CREATE TABLE IF NOT EXISTS fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL CHECK(type IN ('nursery', 'main')),
            area_value REAL,
            area_unit TEXT CHECK(area_unit IN ('m2', 'tan', 'tsubo')),
            spray_rate REAL,          -- 散布率 (L/10a) — 圃場タイプ別の接地ナレッジ
            spray_volume REAL,        -- 散布量 (L) — 導出値 = spray_rate × 面積(beds)
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

        -- マルチ資材DB（mulch_materials）— 仕様決定の緒言（候補プール）。
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

        -- マルチ在庫（mulch_inventory）— 在庫確認の緒言（数量型）。
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

        -- 従業者マスター（workers）— 全作業の「誰」（担当者/業者）。DC の油交換の「業者」と同型。
        -- 名前＋役割＋電話（結果が後日電話で来る連絡先）＋所属。汎用（検鏡/定植/散布/巡回…）。
        CREATE TABLE IF NOT EXISTS workers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,       -- 従業者名
            role        TEXT,                          -- 役割（検鏡/定植/散布/巡回…）
            phone       TEXT,                          -- 電話
            org         TEXT,                          -- 所属（那賀地方いちご生産組合連合会 / JA紀の里…）
            created_at  TEXT,
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_workers_name ON workers(name);

        -- コホート（イチゴ栽培サイクル）マスター。1 コホート = 1 大きなまとまり
        -- （育苗→熱消毒→定植→収穫→廃棄 の年をまたぐサイクル）= 1 プロジェクト。
        -- 命名は「第N期」（通算番号・年またぎに強い）。収穫年・育苗開始年月は属性。
        -- 第N期 = 収穫年（西暦）で 1 対 1（例: 第5期 = 令和8年(2026)収穫・2025-11 育苗開始）。
        CREATE TABLE IF NOT EXISTS cohorts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL UNIQUE,   -- 表示名（既定: 第N期）
            period_no       INTEGER,                   -- 第N期（通算番号）
            harvest_year    INTEGER,                   -- 収穫年（西暦・例 2026）
            seedling_start  TEXT,                      -- 育苗開始年月（例 "2025-11"）
            field_id        INTEGER REFERENCES fields(id), -- 関連圃場（育苗圃）
            notes           TEXT,
            created_at      TEXT,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cohorts_name ON cohorts(name);

        -- 定植暦（transplant_schedule）— 育苗圃苗の「花芽分化確認」を暦として管理。
        -- 防除暦（spray_schedule）と同一パターン（§2.3 の流儀: 暦は1枚・表に cadence で区別）。
        -- 完了判定は**外部組織（那賀地方いちご生産組合連合会）の判定**＝STN の
        -- P_external_wait（外部待ち）プレースに対応。判定記録（judge_*）が reached を決める。
        CREATE TABLE IF NOT EXISTS transplant_schedule (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_date   TEXT    NOT NULL,          -- 花芽分化確認予定日 YYYY-MM-DD
            start_time      TEXT,                      -- 開始時刻 HH:MM（NULL=未指定）
            end_time        TEXT,                      -- 終了時刻 HH:MM（NULL=未指定）
            judge_date      TEXT,                      -- 判定日（外部組織が判定した日・NULL=未判定）
            judge_result    TEXT,                      -- 判定結果（分化あり/分化なし）
            judge_org       TEXT,                      -- 判定組織（既定: 那賀地方いちご生産組合連合会）
            judge_location  TEXT,                      -- 検査場所（JA紀の里地域本部 / JA紀の里地域本部ふるさとセンター）
            cohort_id       INTEGER REFERENCES cohorts(id),   -- 所属コホート（育苗バッチ・NULL=単発）
            seq             INTEGER,                   -- 検鏡の順序（コホート内 1,2,3…）
            is_final        INTEGER DEFAULT 0,         -- 最終検鏡フラグ（NG でも定植に進む）
            assignee_id     INTEGER REFERENCES workers(id),   -- 持ち込む誰（従業者・単一・後方互換）
            assignee_ids    TEXT,                      -- 持ち込み担当（従業者IDのJSON配列・複数選択可）
            status          TEXT    NOT NULL DEFAULT 'scheduled'
                        CHECK(status IN ('scheduled', 'confirmed', 'pending', 'missed')),
            notes           TEXT,                      -- 備考（品種・株数・圃場など）
            field_id        INTEGER REFERENCES fields(id),
            created_at      TEXT,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_transplant_schedule_date ON transplant_schedule(schedule_date);
        CREATE INDEX IF NOT EXISTS idx_transplant_schedule_status ON transplant_schedule(status);
        -- 注: idx_transplant_schedule_cohort は cohort_id 列が既存DBに無いと失敗するため
        -- 下方の migration（ALTER 後）で作成する。

        -- 検鏡場所マスター（inspection_locations）— 花芽分化の顕微鏡検査を行う場所。
        -- 圃場マスター（fields）と同型の名前マスター。transplant_schedule.judge_location
        -- がこの表の名前を参照する（検鏡場所を登録メンテ UI で管理）。
        CREATE TABLE IF NOT EXISTS inspection_locations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,       -- 場所名（JA紀の里地域本部 等）
            created_at  TEXT,
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_inspection_locations_name ON inspection_locations(name);

        -- 事業主体・実働主体マスター（operators）— 主体（subject）と実働（operator）。
        -- 事業主体（三果倉）は要求の発生源（接地の根・4-level ID の subject_id）。
        -- 実働（従業員・機械・アプリ）は作動を実行する側。type で区別（subject / operator）。
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

        -- 役割（roles）— 位置的角色（事業主・店長・課長…）のノード。
        -- 役割は「位置（ポスト）」そのもので、第一級エンティティ（entity_id='role'）として
        -- 建模する（ts/真界_事業主体設計.md §2.6）。要求の発生源は役割（人物でなく）。
        -- 紐付け（誰が保持しているか）は HUMOS の delegation（role --[holds]--> worker）に置く。
        -- 世代交代 = holds エッジの差し替えのみ（本表の役割ノードは不変）。
        CREATE TABLE IF NOT EXISTS roles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,   -- 事業主 / 店長 / 部門長 / 課長…
            subject_id  TEXT    NOT NULL DEFAULT 'mikakura',
            notes       TEXT,
            created_at  TEXT,
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_roles_name ON roles(name);

        -- 定植（コホートの終端・下流の作動）。判定次第で日付が自動計算される（ブリッジ導出）。
        -- この表は手動オーバーライド（完了 status / 従業者 / 手動日付）用。
        CREATE TABLE IF NOT EXISTS transplant_plan (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            cohort_id     INTEGER NOT NULL REFERENCES cohorts(id),
            schedule_date TEXT,                        -- 定植予定日（NULL=自動導出・手動上書き可）
            assignee_id   INTEGER REFERENCES workers(id),   -- 単一・後方互換
            assignee_ids  TEXT,                        -- 定植担当（従業者IDのJSON配列・複数選択可）
            status        TEXT    NOT NULL DEFAULT 'scheduled'
                            CHECK(status IN ('scheduled', 'done', 'missed')),
            notes         TEXT,
            created_at    TEXT,
            updated_at    TEXT
        );

        -- 熱消毒予定（heat_schedule）— コホートのベッド準備（熱消毒）の着手日・期限日・担当・実績。
        -- 1 コホート 1 熱消毒。定植の1か月前に着手し3週間以上実施する前段フェーズ。
        -- 日付は手動指定（自動導出なし）。
        CREATE TABLE IF NOT EXISTS heat_schedule (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            cohort_id     INTEGER NOT NULL REFERENCES cohorts(id),
            start_date    TEXT,                        -- 熱消毒着手日 YYYY-MM-DD（例: 第5期=2026-08-16）
            deadline_date TEXT,                        -- 完了期限日 YYYY-MM-DD（手動指定）
            assignee_id   INTEGER REFERENCES workers(id),   -- 単一・後方互換
            assignee_ids  TEXT,                        -- 熱消毒担当（従業者IDのJSON配列・複数選択可）
            status        TEXT    NOT NULL DEFAULT 'scheduled'
                            CHECK(status IN ('scheduled', 'started', 'done', 'missed')),
            notes         TEXT,
            created_at    TEXT,
            updated_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_transplant_plan_cohort ON transplant_plan(cohort_id);

        CREATE TABLE IF NOT EXISTS inventory (
            id TEXT PRIMARY KEY,
            pesticideId TEXT NOT NULL REFERENCES pesticides(id),
            productName TEXT NOT NULL,
            lotNumber TEXT,
            quantity REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'ml',
            expiryDate TEXT,
            supplier TEXT,
            purchaseDate TEXT,
            notes TEXT,
            field_id INTEGER REFERENCES fields(id),
            createdAt TEXT NOT NULL,
            updatedAt TEXT NOT NULL
        );

        -- ── 境界言語DSL 自動導出用テーブル（共通上位形式・STB）─────────────
        -- DC（発電機オイル）と同一の rbp_* 知識テーブル。判断知識（ブリッジ・
        -- スコア・BOX・認知軸・ゲート・投射）を形式知として DB に格納し、
        -- scripts/derive_bnf.py / scripts/rbp_engine.py が DB から境界言語・
        -- RBP 行列値を自動導出する（クローズドループ）。DDL は db_setup.py と
        -- 同一（既存の二重管理の流儀）。
        CREATE TABLE IF NOT EXISTS rbp_domain (
            domain_id        TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            layer_specbridge TEXT NOT NULL DEFAULT 'absent'
                               CHECK(layer_specbridge IN ('present','absent')),
            fold_order       TEXT NOT NULL DEFAULT 'declaration-order'
                               CHECK(fold_order IN ('level-ascending','declaration-order')),
            -- <Spec-Profile>.kind（候補選択 STB / 条件判定 DC）＋候補プールの指針
            spec_kind        TEXT NOT NULL DEFAULT 'condition-decision'
                               CHECK(spec_kind IN ('candidate-selection','condition-decision')),
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

        -- STB のブリッジは述語ベース（rule_json）。DC の閾値ベース（dim/open_at）
        -- と共存させるため dim/open_at は NULL 可、rule_json を追加。
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
            task_id       TEXT,
            UNIQUE(domain_id, cond_name, task_id)
        );

        CREATE TABLE IF NOT EXISTS rbp_spec_gate (
            domain_id  TEXT PRIMARY KEY REFERENCES rbp_domain(domain_id),
            task_id    TEXT,                          -- タスク=業務単位（NULL=未割当・後方互換）
            pre        TEXT,
            open_expr  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rbp_spec_decision (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id         INTEGER REFERENCES fields(id),
            as_of            TEXT NOT NULL,
            entry_vector     TEXT,
            eval_box_id      TEXT,
            status           TEXT,
            best_set         TEXT,
            best_score       REAL,
            mirror_id        REAL,
            alternatives     TEXT,
            bridge_trace     TEXT,
            spec_json        TEXT,
            source           TEXT
                               CHECK(source IN ('engine','fallback','manual','backfill')),
            created_at       TEXT DEFAULT (datetime('now')),
            updated_at       TEXT DEFAULT (datetime('now')),
            UNIQUE(field_id, as_of)
        );

        CREATE INDEX IF NOT EXISTS idx_rbp_spec_decision_field ON rbp_spec_decision(field_id);
        CREATE INDEX IF NOT EXISTS idx_rbp_spec_decision_asof  ON rbp_spec_decision(as_of);

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

        -- HUMOS（土壌 / 状態空間管理基盤）— 真界ループの状態空間（DC/STB 共通OS）
        -- 5 テーブル（humos_os.schema と同一の DDL）。
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
    """)
    conn.commit()

    # net_id 移行（"stb" → "mikakura"）— オントロジー根底改修（net_id = subject_id）。
    # HUMOS 4 テーブル（token/marking/firing/writing）の net_id を一括移行（冪等）。
    # place テーブルは net_id なし（place_id PK）。humos.py の _NET_ID と**同一再起動
    # サイクル**で変更する（片方だけだとネットが発火しない・原子的結合）。
    for _net_tbl in ("token", "marking", "firing", "writing"):
        _n = conn.execute(
            f"UPDATE {_net_tbl} SET net_id='mikakura' WHERE net_id='stb'").rowcount
        if _n:
            print(f"  migrate: net_id 'stb'→'mikakura' in {_net_tbl} ({_n} rows)")
    conn.commit()

    # スケジュールの所有者を主体へ（オントロジー根底改修・ts/真界_事業主体設計.md §6）。
    # マルチ敷設・定植の schedule_def の target_key を field→subject に移行（冪等）。
    # 圃場（本圃）は要求トークンの文脈（context）に格降格する。
    _sched_tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schedule_def'").fetchone()
    if _sched_tbl:
        _n = conn.execute(
            "UPDATE schedule_def SET target_key='subject' "
            "WHERE target_key='field' AND name IN ('マルチ敷設','定植')").rowcount
        if _n:
            print(f"  migrate: schedule_def target_key 'field'→'subject' ({_n} rows)")
        # R1: 圃場巡回点検（daily-check）も主体所有（§6・§11.4 R）。旧「圃場が主体」
        # モデルの残骸を除去。target_value（A-1）は対象（文脈）のまま。
        _n2 = conn.execute(
            "UPDATE schedule_def SET target_key='subject' "
            "WHERE target_key='field' AND name='圃場巡回点検'").rowcount
        if _n2:
            print(f"  migrate: schedule_def target_key 'field'→'subject' (圃場巡回点検 {_n2} rows)")
        # net_id 移行（"stb" → "mikakura"）— schedule_def も net_id=subject_id（§13.1）。
        # token/marking/firing/writing は上の一括移行で済むが、schedule_def は net_id
        # 変更**前**に UI（POST /api/operations/defs・旧 net_id="stb"）で作られた行が
        # 残ると、server が net_id="mikakura" で照会（plan.expected_state_at）して
        # 業務インスタンス（圃場巡回点検・マルチ敷設・定植）が 0 件になり、STN の
        # 膜弧（マルチ敷設・定植要求）が描画されない。冪等移行で残骸を除去。
        _n3 = conn.execute(
            "UPDATE schedule_def SET net_id='mikakura' WHERE net_id='stb'").rowcount
        if _n3:
            print(f"  migrate: schedule_def net_id 'stb'→'mikakura' ({_n3} rows)")
        conn.commit()

    # 既存DBへの field_id カラム追加（fields テーブル作成後、idempotent）。
    migrate_add_field_id(conn)

    # 既存DBへの <Spec-Profile> 列追加（spec_kind / candidate_pool*）。
    # CREATE TABLE IF NOT EXISTS では既存テーブルに列が追えないため、
    # PRAGMA table_info で確認し無ければ ALTER で追加する（idempotent）。
    _migrate_rbp_domain_spec_kind(conn)

    # rbp_spec_condition の UNIQUE を (domain_id, cond_name, task_id) に再構築。
    # 各タスクが同名の仕様条件（EFFECTIVENESS 等）を持つため（idempotent）。
    # _migrate_rbp_task（mulch 条件 INSERT）より先に走らせる必要がある。
    _migrate_rbp_spec_condition_unique(conn)

    # rbp_eval_box に context（要求評価グループの文脈: 定植/熱消毒…）追加（idempotent）。
    # マルチ選定の要求評価は「(context × mulch_type) のグループ」で要求を分類し、
    # グループの参照ベクトル（members）と要求ベクトルのコサイン類似度=ミラーID を付与する
    # （ユーザー指定 2026-09-07: 「定植白黒マルチグループが選択される、ミラーID」）。
    # rx-select（薬剤選定）は context=NULL（後方互換）。
    # _migrate_rbp_task（mulch BOX seed が context 列を参照）より先に走らせる。
    _eb_cols = {r[1] for r in conn.execute("PRAGMA table_info(rbp_eval_box)")}
    if _eb_cols and "context" not in _eb_cols:
        conn.execute("ALTER TABLE rbp_eval_box ADD COLUMN context TEXT")
        conn.commit()
        print("  migrate: ALTER TABLE rbp_eval_box ADD COLUMN context")

    # 既存DBへの task_id（タスク=業務単位）追加 + rbp_task/rbp_actuation 新設（idempotent）。
    _migrate_rbp_task(conn)

    # 既存DBへの pesticides 商品マスター列追加（brand/formulation/applicationRate/
    # packSize/packUnit/price）+ P01 の inventory 由来 seed（idempotent）。
    _migrate_pesticide_product_master(conn)

    # 既存DBへの fields 散布属性追加（spray_rate / spray_volume）+ 圃場タイプ別の
    # 散布量既定値（育苗圃=50L / 本圃=500L）を圃場ごとに直接接地（idempotent）。
    _migrate_field_spray(conn)

    # 既存DBへの transplant_schedule 列追加（idempotent）。
    # judge_location=花芽分化の検査場所（JA紀の里地域本部 / ふるさとセンター）。
    # start_time / end_time=確認の開始・終了時刻（何時から何時）。
    _tp_cols = {r[1] for r in conn.execute("PRAGMA table_info(transplant_schedule)")}
    if _tp_cols:
        _added = False
        if "judge_location" not in _tp_cols:
            conn.execute("ALTER TABLE transplant_schedule ADD COLUMN judge_location TEXT")
            _added = True
        if "start_time" not in _tp_cols:
            conn.execute("ALTER TABLE transplant_schedule ADD COLUMN start_time TEXT")
            _added = True
        if "end_time" not in _tp_cols:
            conn.execute("ALTER TABLE transplant_schedule ADD COLUMN end_time TEXT")
            _added = True
        # コホート連携（検鏡鎖＋定植）: cohort_id/seq/is_final/assignee_id
        if "cohort_id" not in _tp_cols:
            conn.execute("ALTER TABLE transplant_schedule ADD COLUMN cohort_id INTEGER")
            _added = True
        if "seq" not in _tp_cols:
            conn.execute("ALTER TABLE transplant_schedule ADD COLUMN seq INTEGER")
            _added = True
        if "is_final" not in _tp_cols:
            conn.execute("ALTER TABLE transplant_schedule ADD COLUMN is_final INTEGER DEFAULT 0")
            _added = True
        if "assignee_id" not in _tp_cols:
            conn.execute("ALTER TABLE transplant_schedule ADD COLUMN assignee_id INTEGER")
            _added = True
        if _added:
            conn.commit()
            print("  migrate: ALTER TABLE transplant_schedule ADD COLUMN (judge_location/start_time/end_time/cohort_id/seq/is_final/assignee_id)")
    # cohort_id 列が確定した後にインデックスを作成（既存DBは ALTER 後・新DBは DDL 済み）。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transplant_schedule_cohort ON transplant_schedule(cohort_id)")
    conn.commit()

    # 既存DBへの 主体/組織 FK 列追加（idempotent）— オントロジー根底改修（§11.2）。
    # workers.operator_id=所属・fields.operator_id=権利者・transplant_schedule.judge_org_id=判定組織。
    _fk_cols_w = {r[1] for r in conn.execute("PRAGMA table_info(workers)")}
    if _fk_cols_w and "operator_id" not in _fk_cols_w:
        conn.execute("ALTER TABLE workers ADD COLUMN operator_id INTEGER REFERENCES operators(id)")
        conn.commit()
        print("  migrate: ALTER TABLE workers ADD COLUMN operator_id")
    _fk_cols_f = {r[1] for r in conn.execute("PRAGMA table_info(fields)")}
    if _fk_cols_f and "operator_id" not in _fk_cols_f:
        conn.execute("ALTER TABLE fields ADD COLUMN operator_id INTEGER REFERENCES operators(id)")
        conn.commit()
        print("  migrate: ALTER TABLE fields ADD COLUMN operator_id")
    _fk_cols_t = {r[1] for r in conn.execute("PRAGMA table_info(transplant_schedule)")}
    if _fk_cols_t and "judge_org_id" not in _fk_cols_t:
        conn.execute("ALTER TABLE transplant_schedule ADD COLUMN judge_org_id INTEGER REFERENCES organizations(id)")
        conn.commit()
        print("  migrate: ALTER TABLE transplant_schedule ADD COLUMN judge_org_id")

    # 事業主体・組織の冪等シード（idempotent）— オントロジー根底改修（§11.2）。
    # 事業主体（三果倉）= 要求の発生源（4-level ID の subject_id）。
    # 組織（那賀地方いちご生産組合連合会=判定 / JA紀の里=管理）= 自由記述の組織名をオブジェクト化。
    conn.execute(
        "INSERT INTO operators (name, type, owner_name) "
        "SELECT '三果倉', 'subject', '松山浩士' "
        "WHERE NOT EXISTS (SELECT 1 FROM operators WHERE name='三果倉')")
    conn.execute(
        "INSERT INTO organizations (name, role) "
        "SELECT '那賀地方いちご生産組合連合会', 'judge' "
        "WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE name='那賀地方いちご生産組合連合会')")
    conn.execute(
        "INSERT INTO organizations (name, role) "
        "SELECT 'JA紀の里', 'manage' "
        "WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE name='JA紀の里')")
    conn.commit()
    # 既存行の FK バックフィル（単一主体 STB・idempotent）。
    _subj_id = conn.execute(
        "SELECT id FROM operators WHERE name='三果倉'").fetchone()
    if _subj_id:
        _subj_id = _subj_id[0]
        conn.execute("UPDATE workers SET operator_id=? WHERE operator_id IS NULL", (_subj_id,))
        conn.execute("UPDATE fields SET operator_id=? WHERE operator_id IS NULL", (_subj_id,))
    _judge_org = conn.execute(
        "SELECT id FROM organizations WHERE name='那賀地方いちご生産組合連合会'").fetchone()
    if _judge_org:
        _judge_org = _judge_org[0]
        conn.execute(
            "UPDATE transplant_schedule SET judge_org_id=? "
            "WHERE judge_org_id IS NULL AND judge_org='那賀地方いちご生産組合連合会'",
            (_judge_org,))
    conn.commit()
    print("  seed: 事業主体(三果倉)・組織(那賀地方…連合会/JA紀の里)・FK バックフィル")

    # スケジュール所有者の主体化（オントロジー根底改修・ts/真界_事業主体設計.md §6）。
    # 業務の所有者=事業主体（subject_id）、圃場（field_id）は対象パラメータとして維持。
    # owner 保持テーブルに subject_id 列を追加し、単一主体 STB では事業主体へバックフィル。
    _subject_op = conn.execute(
        "SELECT id FROM operators WHERE name='三果倉' AND type='subject'").fetchone()
    _subject_op = _subject_op[0] if _subject_op else None
    for _owner_tbl in ("spray_schedule", "transplant_schedule", "cohorts",
                       "beds", "mulch_inventory", "inventory"):
        _oc = {r[1] for r in conn.execute(f"PRAGMA table_info({_owner_tbl})")}
        if _oc and "subject_id" not in _oc:
            conn.execute(f"ALTER TABLE {_owner_tbl} ADD COLUMN subject_id INTEGER REFERENCES operators(id)")
            conn.commit()
            print(f"  migrate: ALTER TABLE {_owner_tbl} ADD COLUMN subject_id")
    if _subject_op:
        for _owner_tbl in ("spray_schedule", "transplant_schedule", "cohorts",
                           "beds", "mulch_inventory", "inventory"):
            conn.execute(
                f"UPDATE {_owner_tbl} SET subject_id=? WHERE subject_id IS NULL",
                (_subject_op,))
        conn.commit()
        print("  seed: スケジュール所有者 subject_id バックフィル（事業主体 三果倉）")

    # 既存DBへの cohorts 列追加（idempotent）: 第N期・収穫年・育苗開始年月。
    _co_cols = {r[1] for r in conn.execute("PRAGMA table_info(cohorts)")}
    if _co_cols:
        _co_added = False
        if "period_no" not in _co_cols:
            conn.execute("ALTER TABLE cohorts ADD COLUMN period_no INTEGER")
            _co_added = True
        if "harvest_year" not in _co_cols:
            conn.execute("ALTER TABLE cohorts ADD COLUMN harvest_year INTEGER")
            _co_added = True
        if "seedling_start" not in _co_cols:
            conn.execute("ALTER TABLE cohorts ADD COLUMN seedling_start TEXT")
            _co_added = True
        if _co_added:
            conn.commit()
            print("  migrate: ALTER TABLE cohorts ADD COLUMN (period_no/harvest_year/seedling_start)")

    # 既存DBへの heat_schedule.status に 'started'（消毒開始）を追加（idempotent）。
    # CHECK 制約は ALTER では変えられないため、旧制約（scheduled/done/missed のみ）の
    # テーブルは 12 手順で再構築する（新DBは CREATE 済みで制約が正 → スキップ）。
    _hs_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='heat_schedule'").fetchone()
    if _hs_sql and "'started'" not in _hs_sql[0]:
        _hs_cols = [r[1] for r in conn.execute("PRAGMA table_info(heat_schedule)")]
        conn.execute("ALTER TABLE heat_schedule RENAME TO heat_schedule_old")
        conn.execute(
            """CREATE TABLE heat_schedule (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                cohort_id     INTEGER NOT NULL REFERENCES cohorts(id),
                start_date    TEXT,
                deadline_date TEXT,
                assignee_id   INTEGER REFERENCES workers(id),
                status        TEXT    NOT NULL DEFAULT 'scheduled'
                                CHECK(status IN ('scheduled', 'started', 'done', 'missed')),
                notes         TEXT,
                created_at    TEXT,
                updated_at    TEXT
            )""")
        _hs_col_list = ", ".join(_hs_cols)
        conn.execute(f"INSERT INTO heat_schedule ({_hs_col_list}) SELECT {_hs_col_list} FROM heat_schedule_old")
        conn.execute("DROP TABLE heat_schedule_old")
        conn.commit()
        print("  migrate: heat_schedule.status に 'started'（消毒開始）を追加（テーブル再構築）")

    # 担当者の複数選択: 4作業テーブルに assignee_ids（従業者IDのJSON配列）を追加（idempotent）。
    # 既存の単一 assignee_id があれば、初回のみ assignee_ids=[assignee_id] へ移行する
    # （assignee_ids が未設定の行だけ。以降は UI が assignee_ids を正として扱う）。
    for _t in ("transplant_schedule", "transplant_plan", "heat_schedule", "spray_schedule"):
        _tcols = {r[1] for r in conn.execute(f"PRAGMA table_info({_t})")}
        if "assignee_ids" not in _tcols:
            conn.execute(f"ALTER TABLE {_t} ADD COLUMN assignee_ids TEXT")
            conn.commit()
            print(f"  migrate: ALTER TABLE {_t} ADD COLUMN assignee_ids")
    _migrated = 0
    for _t in ("transplant_schedule", "transplant_plan", "heat_schedule"):
        _rows = conn.execute(
            f"SELECT id, assignee_id FROM {_t} WHERE assignee_ids IS NULL AND assignee_id IS NOT NULL").fetchall()
        for _r in _rows:
            conn.execute(f"UPDATE {_t} SET assignee_ids=? WHERE id=?",
                         (json.dumps([_r[1]]), _r[0]))
        _migrated += len(_rows)
    if _migrated:
        conn.commit()
        print(f"  migrate: 単一 assignee_id を assignee_ids へ移行（{_migrated} 行）")

    # 病害虫DB（唯一無一の正）の self-bootstrap: data/stb.db は gitignore されているため
    # クリーンチェックアウトでは空。diseases テーブルが空なら db_setup.py の
    # DISEASES_SEED（唯一の正）から投入し、/api/diseases と perception.py が
    # 起動時に正しく動作するよう保証する。
    _n = conn.execute("SELECT COUNT(*) FROM diseases").fetchone()[0]
    if _n == 0:
        for _d in DISEASES_SEED:
            conn.execute(
                "INSERT INTO diseases (id, name, type, icon) VALUES (?, ?, ?, ?)",
                _d,
            )
        conn.commit()

    # 薬剤DB（唯一無一の正）の self-bootstrap: data/stb.db が空なら、コミット済みの
    # 生成物 data/pesticides.json（DB から scripts/export_pesticides.py が再生成）から投入。
    # 病害虫（インライン DISEASES_SEED）と違い、薬剤は 67 件・フィールド多のため
    # 生成物スナップショットを bootstrap 源に使う（db_setup.py と同一経路）。
    _pn = conn.execute("SELECT COUNT(*) FROM pesticides").fetchone()[0]
    if _pn == 0:
        _ppath = os.path.join(APP_ROOT, "data", "pesticides.json")
        if os.path.exists(_ppath):
            with open(_ppath, "r", encoding="utf-8") as _pf:
                _ppests = json.load(_pf)
            for _p in _ppests:
                conn.execute(
                    """INSERT OR IGNORE INTO pesticides
                       (id, name, activeIngredient, category, targetVector, targetNames,
                        phiDays, mixingRestriction, mixingBanTargets, maxApplications,
                        toxicityClass, system, systemCode, dilutionRate)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _p["id"], _p["name"], _p.get("activeIngredient"), _p.get("category"),
                        json.dumps(_p.get("targetVector", [])),
                        json.dumps(_p.get("targetNames", [])),
                        _p.get("phiDays"), _p.get("mixingRestriction"),
                        json.dumps(_p.get("mixingBanTargets", [])), _p.get("maxApplications"),
                        _p.get("toxicityClass"), _p.get("system"), _p.get("systemCode"),
                        _p.get("dilutionRate"),
                    ),
                )
            conn.commit()

    # 圃場マスター（唯一無一の正）の self-bootstrap: data/stb.db が空なら
    # db_setup.py の FIELDS_SEED から投入（病害虫の DISEASES_SEED と同経路）。
    _fn = conn.execute("SELECT COUNT(*) FROM fields").fetchone()[0]
    if _fn == 0:
        _now = datetime.datetime.utcnow().isoformat()
        for _fname, _ftype, _farea, _funit, _frate, _fvol in FIELDS_SEED:
            conn.execute(
                "INSERT INTO fields (name, type, area_value, area_unit, spray_rate, spray_volume, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_fname, _ftype, _farea, _funit, _frate, _fvol, _now, _now),
            )
        conn.commit()

    # 検鏡場所マスター（唯一無一の正）の self-bootstrap: data/stb.db が空なら
    # db_setup.py の INSPECTION_LOCATIONS_SEED から投入（圃場の FIELDS_SEED と同経路）。
    _ln = conn.execute("SELECT COUNT(*) FROM inspection_locations").fetchone()[0]
    if _ln == 0:
        _now = datetime.datetime.utcnow().isoformat()
        for _lname in INSPECTION_LOCATIONS_SEED:
            conn.execute(
                "INSERT INTO inspection_locations (name, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (_lname, _now, _now),
            )
        conn.commit()

    # 境界言語の形式知（MODEL/ACTUATION）の self-bootstrap: data/stb.db が空なら
    # db_setup.py の seed_rbp_model（現状コードの確定値）から投入。rbp_* テーブルは
    # 新規に作成するため、rbp_domain が空なら投入（冪等・手編集保護）。
    _rn = conn.execute("SELECT COUNT(*) FROM rbp_domain").fetchone()[0]
    if _rn == 0:
        seed_rbp_model(conn)

    # 圃場名統一（2026-09-14）: 「本圃」は廃止し「本圃」に統一。旧「本圃 緒言データ」
    # シード（白黒2列=延べ400m・幅110cm・在庫440m²）は存在しない圃場を参照する no-op
    # レガシーコードだったため削除。本圃 の実データ（ベッド・在庫）は UI/CRUD が管理。

    # エンティティ層（v1.4.0）— 物理界の実在を HUMOS に接地（4-level ID）。
    # subject_id="mikakura" ⊃ domain_id="iichigo" ⊃ entity_id（code）⊃ object_id。
    # 事業主体（三果倉）・圃場（fields）・薬剤単品（pesticides）・従業者（workers）・
    # 組織（organizations）・場所（inspection_locations）をオブジェクトに同期。
    # 属性の正はドメインDB。委任（delegation）は主体→対象の型付き関係。
    from humos_os import entity
    _SUBJECT_ID = "mikakura"   # 事業主体（三果倉）＝ net_id
    _DOMAIN_ID = "iichigo"     # 事業ドメイン（いちご）
    # 3-level → 4-level 移行（冪等・subject_id 列が既にあると no-op）。
    entity.migrate_to_4level(conn, {"stb": (_SUBJECT_ID, _DOMAIN_ID)})
    entity.ensure_entity_schema(conn)

    def _rows_to_dicts(sql):
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # --- 事業主体（operators type='subject'）を entity に同期 ---
    _subject_rows = _rows_to_dicts(
        "SELECT id, name, type, owner_name FROM operators WHERE type='subject'")
    entity.sync_from_domain(conn, _SUBJECT_ID, _DOMAIN_ID, "subject", _subject_rows,
                            id_key="id", name_key="name",
                            attr_map={"type": "type", "owner_name": "owner_name"})

    # --- 圃場・薬剤単品（fields / pesticides）を entity に同期 ---
    _fields_rows = _rows_to_dicts(
        "SELECT id, name, type, area_value, area_unit, spray_rate, spray_volume FROM fields")
    entity.sync_from_domain(conn, _SUBJECT_ID, _DOMAIN_ID, "field", _fields_rows,
                            id_key="id", name_key="name",
                            attr_map={"type": "type", "area_value": "area_value",
                                      "area_unit": "area_unit", "spray_rate": "spray_rate",
                                      "spray_volume": "spray_volume"})
    _pest_rows = _rows_to_dicts("SELECT * FROM pesticides")
    entity.sync_from_domain(conn, _SUBJECT_ID, _DOMAIN_ID, "pesticide", _pest_rows,
                            id_key="id", name_key="name")

    # --- 従業者（workers）・組織（organizations）・場所（inspection_locations）---
    _worker_rows = _rows_to_dicts(
        "SELECT id, name, role, phone, org FROM workers")
    entity.sync_from_domain(conn, _SUBJECT_ID, _DOMAIN_ID, "worker", _worker_rows,
                            id_key="id", name_key="name",
                            attr_map={"role": "role", "phone": "phone", "org": "org"})
    _org_rows = _rows_to_dicts(
        "SELECT id, name, role, person_in_charge, tel, fax FROM organizations")
    entity.sync_from_domain(conn, _SUBJECT_ID, _DOMAIN_ID, "organization", _org_rows,
                            id_key="id", name_key="name",
                            attr_map={"role": "role", "person_in_charge": "person_in_charge",
                                      "tel": "tel", "fax": "fax"})
    _loc_rows = _rows_to_dicts("SELECT id, name FROM inspection_locations")
    entity.sync_from_domain(conn, _SUBJECT_ID, _DOMAIN_ID, "location", _loc_rows,
                            id_key="id", name_key="name")

    # --- 構成品（§1.5）— オブジェクト（個体）に所属する部品。エンティティには昇格しない。---
    # ① ベッド（本圃 の構成品）— beds テーブルの実データを同期（属性の正は beds）。
    _bed_rows = _rows_to_dicts(
        "SELECT id, field_id, name, seq, length_m, width_m, mulch_type FROM beds")
    _bed_n = 0
    for _b in _bed_rows:
        _fid = str(_b.get("field_id"))
        if not _fid:
            continue
        entity.upsert_component(conn, _SUBJECT_ID, _DOMAIN_ID, "field", _fid, "bed",
                                comp_id=str(_b["id"]), name=_b.get("name"),
                                attrs={"seq": _b.get("seq"),
                                       "length_m": _b.get("length_m"),
                                       "width_m": _b.get("width_m"),
                                       "mulch_type": _b.get("mulch_type")})
        _bed_n += 1

    # ② プランタン・育苗棚（育苗圃 の構成品）— 設計で定義された属性構造を宣言。
    #    値は未入力（個数管理/属性管理の枠）。データソースが確定すれば値を埋める。
    entity.upsert_component(conn, _SUBJECT_ID, _DOMAIN_ID, "field", "1", "planter",
                            comp_id="p1", name="プランタン", attrs={"count": None})
    entity.upsert_component(conn, _SUBJECT_ID, _DOMAIN_ID, "field", "1", "rack",
                            comp_id="r1", name="育苗棚",
                            attrs={"height": None, "width": None, "depth": None,
                                   "row_count": None, "row_length": None})

    # --- 委任（delegation）— 主体→対象の型付き関係（§2.5 委任は3つを生む）---
    # 主体→圃場=own（所有）・主体→従業者=employ（所属・実行者）・主体→組織=mandate（委任）。
    _subj_obj = _subject_rows[0]["id"] if _subject_rows else "1"
    for _f in _fields_rows:
        entity.sync_delegation(conn, _SUBJECT_ID, _DOMAIN_ID, "subject", str(_subj_obj),
                               "own", _SUBJECT_ID, _DOMAIN_ID, "field", str(_f["id"]))
    for _w in _worker_rows:
        entity.sync_delegation(conn, _SUBJECT_ID, _DOMAIN_ID, "subject", str(_subj_obj),
                               "employ", _SUBJECT_ID, _DOMAIN_ID, "worker", str(_w["id"]))
    for _o in _org_rows:
        entity.sync_delegation(conn, _SUBJECT_ID, _DOMAIN_ID, "subject", str(_subj_obj),
                               "mandate", _SUBJECT_ID, _DOMAIN_ID, "organization", str(_o["id"]))

    # --- 役割（roles）— 位置的角色のシードと紐付け（§2.6）---
    # 役割は第一級エンティティ（entity_id='role'）。要求の発生源は役割（人物でなく）。
    # 3ノード鎖: subject --[has_role]--> role --[holds]--> worker。
    # 初期保持者 = operators.owner_name（三果倉=松山浩士）に一致する worker。
    # 再起動で毎回シードするため、holds は**未設定の役割のみ**に設定する
    # （世代交代後の再起動で保持者が元に戻らないよう）。
    _owner_name = _subject_rows[0].get("owner_name") if _subject_rows else None
    # init_db の conn は row_factory 未設定（tuple を返す）→ インデックスアクセス。
    _role_seed = conn.execute(
        "SELECT id FROM workers WHERE name=? ORDER BY id LIMIT 1", (_owner_name,)).fetchone()
    _role_seed_id = str(_role_seed[0]) if _role_seed else None
    if not conn.execute("SELECT 1 FROM roles WHERE name='事業主'").fetchone():
        conn.execute(
            "INSERT INTO roles (name, subject_id, created_at, updated_at) "
            "VALUES ('事業主', ?, datetime('now'), datetime('now'))", (_SUBJECT_ID,))
        conn.commit()
    _role_rows = _rows_to_dicts(
        "SELECT id, name, subject_id FROM roles")
    entity.sync_from_domain(conn, _SUBJECT_ID, _DOMAIN_ID, "role", _role_rows,
                            id_key="id", name_key="name",
                            attr_map={"subject_id": "subject_id"})
    for _r in _role_rows:
        _rid = str(_r["id"])
        # 主体→役割（has_role）— 常に upsert。
        entity.sync_delegation(conn, _SUBJECT_ID, _DOMAIN_ID, "subject", str(_subj_obj),
                               "has_role", _SUBJECT_ID, _DOMAIN_ID, "role", _rid)
        # 役割→人（holds）— 未設定の役割のみ（世代交代を尊重）。
        # init_db の conn は row_factory 未設定（tuple）→ entity.role_holder（dict(r)
        # を使う）は使えず、holds の有無は直接 SQL で確認する。assign_role は行を読ま
        # ない（DELETE+sync_delegation）ため plain conn で問題ない。
        _has_holds = conn.execute(
            "SELECT 1 FROM delegation WHERE subject_id=? AND domain_id=? "
            "AND entity_id='role' AND object_id=? AND rel='holds' LIMIT 1",
            (_SUBJECT_ID, _DOMAIN_ID, _rid)).fetchone()
        if _role_seed_id and not _has_holds:
            entity.assign_role(conn, _SUBJECT_ID, _DOMAIN_ID, _rid, _role_seed_id)

    print(f"  entity: 主体{len(_subject_rows)}件・圃場{len(_fields_rows)}件・"
          f"薬剤単品{len(_pest_rows)}件・従業者{len(_worker_rows)}件・"
          f"組織{len(_org_rows)}件・場所{len(_loc_rows)}件・役割{len(_role_rows)}件・"
          f"構成品(ベッド{_bed_n}+プランタン1+育苗棚1)を同期（v1.5.0・4-level）")

    conn.close()


# --- POST /api/prescribe: RBPエンジン切替（python / haskell） ---
PY_ENGINE_DIR = os.path.join(APP_ROOT, "rbp-algebra-python")
_py_engine = None  # lazy-load cache


def run_python_engine(entry_vector):
    global _py_engine
    if _py_engine is None:
        sys.path.insert(0, PY_ENGINE_DIR)
        import api as _py_engine_mod
        _py_engine = _py_engine_mod
    return _py_engine.prescribe(entry_vector)


def find_haskell_bin():
    hits = []
    for build_root in ("dist-newstyle-user", "dist-newstyle"):
        pattern = os.path.join(
            APP_ROOT, "rbp-algebra", build_root, "build", "*", "*",
            "rbp-algebra-*", "x", "rbp-algebra", "build", "rbp-algebra", "rbp-algebra")
        hits.extend(glob.glob(pattern))
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def run_haskell_engine(entry_vector):
    bin_path = find_haskell_bin()
    if bin_path is None:
        return {"error": "Haskellバイナリが見つかりません（rbp-algebra/ で cabal build が必要）"}
    csv = ",".join(str(v) for v in entry_vector)
    try:
        proc = subprocess.run(
            [bin_path, "--prescribe", csv],
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"error": "Haskellエンジンがタイムアウトしました"}
    if proc.returncode != 0:
        return {"error": f"Haskellエンジンが異常終了しました: {proc.stderr[:500]}"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": f"HaskellエンジンのJSON出力を解析できません: {proc.stdout[:200]}"}


# ─── LangGraph Token Store (Petri net model) ─────────────────────
# トークン集約ノードの状態をメモリ上に保持。
# クライアント（スケジュールタイマー / カレンダーUI）がトークンを投入。
# 全トークンが揃うまで実働は待機。
#
# 実体はトップレベル state.py（状態＝Petri網のプレース）にあり、
# server.py と state_node が共有する同一シングルトン。

from state import set_token, get_token_state, reset_tokens, get_required_keys
import jobnet
import humos
from humos_os import plan  # PLAN レイヤー（期待状態 Expected State(T)）
from humos_os import entity  # エンティティ層（物理界の実在・4-level ID・役割）

# 境界言語図表化（rbp_viz）— DC/STB 共通の共有パッケージ（/app/rbp_viz）。
# humos の import が /app を path に加えるが、明示的に保証して /viz で使う。
_APP_DIR = os.path.dirname(APP_ROOT)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
import rbp_viz  # noqa: E402


# ─── LangGraph Designer Storage ─────────────────────────────────
import os
import shutil

GRAPHS_DIR = os.path.join(APP_ROOT, "data", "designer_graphs")
os.makedirs(GRAPHS_DIR, exist_ok=True)

_graph_lock = threading.Lock()


def _save_graph(name: str, data: dict) -> str:
    """Save a graph JSON file. Returns filename."""
    filename = f"{slugify(name)}.json"
    filepath = os.path.join(GRAPHS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filename


def _load_graph(filename: str) -> dict | None:
    """Load a graph JSON file."""
    filepath = os.path.join(GRAPHS_DIR, filename)
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_graphs() -> list:
    """List all saved graph filenames with metadata."""
    results = []
    for fname in sorted(os.listdir(GRAPHS_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(GRAPHS_DIR, fname)
        stat = os.stat(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Support both old (nodes/edges) and new (stages) formats
            node_count = len(data.get("nodes", [])) or len(data.get("stages", []))
            edge_count = len(data.get("edges", []))
            name = data.get("_meta", {}).get("name", fname.replace(".json", ""))
        except Exception:
            name = fname.replace(".json", "")
            node_count = "?"
            edge_count = "?"
        results.append({
            "filename": fname,
            "name": name,
            "nodeCount": node_count,
            "edgeCount": edge_count,
            "size": stat.st_size,
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return results


def _delete_graph(filename: str) -> bool:
    """Delete a graph file."""
    filepath = os.path.join(GRAPHS_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def slugify(s: str) -> str:
    """Simple slugify: lowercase, alphanumeric + hyphens + unicode."""
    import re
    s = re.sub(r"[^\w\-]", "-", s).lower()
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


# ─── プレースへのトークン投入（発火接続）────────────────────────
# 防除暦「⚡今すぐ」/ cron（_handle_spray_schedule_generate）が処方生成時に、
# ①イベント発生→②病害虫予測ベクトル生成 の結果を Petri 網のプレース（state.py
# の単一トークンストア）へ投入する。圃場種トークン（fields.type 由来）と、
# 認知で生成した病害虫予測行列トークン（set_ids→BOX.vector）の2つ。
_FIELD_TYPE_LABEL = {"nursery": "🌱 育苗", "main": "🌾 本圃"}
_AREA_UNIT_LABEL = {"m2": "m²", "tan": "反", "tsubo": "坪"}


def _build_field_type_token(field_id, conn):
    """field_id を fields テーブルから解決し、圃場種トークンの文字列を返す。

    例: "🌱 育苗 育苗圃（200m²）" / 解決不可・field_id 未設定は None。
    """
    if not field_id:
        return None
    row = conn.execute(
        "SELECT name, type, area_value, area_unit FROM fields WHERE id=?", (field_id,)
    ).fetchone()
    if row is None:
        return None
    label = _FIELD_TYPE_LABEL.get(row["type"], row["type"])
    # 面積表示
    area = ""
    if row["area_value"] is not None and row["area_unit"]:
        area = f"（{row['area_value']}{_AREA_UNIT_LABEL.get(row['area_unit'], row['area_unit'])}）"
    return f"{label} {row['name']}{area}"


def _build_pest_matrix_token(row, box_vectors):
    """②病害虫予測ベクトル生成を再現し、予測行列トークンの文字列を返す。

    set_ids の「セットN」→ BOX-NN.vector（= rx_prescribe.stage② と同一の行列参照）。
    生成不可（セット未設定/BOX未対応/空）は None。
    """
    set_ids = json.loads(row["set_ids"]) if row["set_ids"] else []
    set_num = None
    for s in set_ids:
        m = re.search(r"セット(\d+)", str(s))
        if m:
            set_num = int(m.group(1))
            break
    if not set_num:
        return None
    box = box_vectors.get(f"BOX-{set_num:02d}")
    if not box:
        return None
    vec = box.get("vector")
    if not vec:
        return None
    return json.dumps(vec)


def _fire_place_tokens(field_id, row, box_vectors):
    """発火時にプレースへ圃場種・病害虫予測行列の2トークンを投入する。

    投入不可のトークンはスキップ（発火の失敗にはしない — 処方生成の主経路を止めない）。
    戻り値: {field_type: str|None, pest_matrix: str|None}（投入済みの値）。
    """
    injected = {"field_type": None, "pest_matrix": None}
    conn = get_db()
    try:
        field_token = _build_field_type_token(field_id, conn)
    finally:
        conn.close()
    matrix_token = _build_pest_matrix_token(row, box_vectors)
    if field_token:
        set_token("field_type", field_token)
        injected["field_type"] = field_token
    if matrix_token:
        set_token("pest_matrix", matrix_token)
        injected["pest_matrix"] = matrix_token
    return injected


def _run_perception_check(field_token, matrix_token):
    """認知（第一トランジション）の OK/NG 判定を実行して構造化結果を返す。

    認知（perception.py）はプレースに入った2トークンに対し、
      - check_presence: トークンのそろい（field_type / pest_matrix が揃っているか）
      - check_vector:   行列チェック（10次元・2値・トークン1個以上）
    を行い、合格すれば発火を許す。ここはその結果を「OK / NG + 理由」で返す。

    Returns:
        {"ok": bool, "label": "OK"|"NG", "reason": str|None,
         "presence": {"ok": bool, "reason": str|None},
         "vector":   {"ok": bool, "reason": str|None}}
    """
    import perception

    # ① トークンのそろいチェック
    presence_ok, presence_err = perception.check_presence({
        "field_type": field_token,
        "pest_matrix": matrix_token,
    })

    # ② 行列チェック（pest_matrix を list に復元して check_vector）
    vector_ok, vector_err = (True, None)
    if matrix_token is None:
        vector_ok, vector_err = (False, "行列トークンなし（セット未設定/BOX未対応）")
    else:
        try:
            vec = json.loads(matrix_token)
        except (json.JSONDecodeError, TypeError):
            vec = None
        if not isinstance(vec, list):
            vector_ok, vector_err = (False, "行列トークンの型不一致（JSON list でない）")
        else:
            vector_ok, vector_err = perception.check_vector(vec)

    ok = presence_ok and vector_ok
    reason = None
    if not ok:
        parts = [e for e in (presence_err, vector_err) if e]
        reason = "、".join(parts)
    return {
        "ok": ok,
        "label": "OK" if ok else "NG",
        "reason": reason,
        "presence": {"ok": presence_ok, "reason": presence_err},
        "vector": {"ok": vector_ok, "reason": vector_err},
    }


# ─── 運航管理センター（operations）AI 解読層 ────────────────────
# 石6・AIフェーズ。決定論的な Expected/Actual 比較（humos_os.plan）の上に
# 「記述・因果・予測・パターン・提案」の自然言語解読を載せる。
# 設計: ts/運航管理センターUI設計.md §3.4。
#
# 分業: OS は決定論的な比較まで担い、解釈は生成 AI。
#   入力 = alerts（逸脱中インスタンス）＋ firing_log/sos_log（直近）＋ 在庫・外部コンテキスト
#   出力 = [{title, description, cause, prediction, pattern, action, confidence}]
# AI 未設定時は決定論フォールバックで 5 層を機械生成（UI が空にならない）。

# 同一異常の再解釈は 5 分間隔（コスト抑制・設計 §3.4）。
_AI_INTERPRET_TTL = 300
_ai_interpret_cache = {}          # key -> (monotonic_ts, interpretations)
_ai_interpret_lock = threading.Lock()

_AI_SYSTEM_PROMPT = (
    "あなたは農業防除の運航管理センターの AI 解読層です。"
    "決定論的に検出された異常（Expected State(T) ≠ Actual State(T)）を、"
    "運用チームが「あ、そういうことか」と理解できる自然言語に翻訳します。\n"
    "必ず 5 層の構造で答えてください:\n"
    "① 記述: コードでなく文章で、何が起きているか\n"
    "② 因果: なぜ起きているか（発火ログを遡り詰まり点を特定）\n"
    "③ 予測: このままでは先に何が起きるか（期限到達の見込み）\n"
    "④ パターン: 複数インスタンスを横断して繋ぐ（個人の遅延ではなく系統的制約）\n"
    "⑤ 行動: 具体的な次の一手（代替手段・切替・エスカレーション）\n"
    "データに基づき、推測や事前知識だけで断定しない。日本語で簡潔に。"
)


def _ai_interpret_key(alerts):
    """頻度制限のキー: 逸脱中の（def_id, status）集合。異常が変化した時だけ再解釈。"""
    sig = sorted((a.get("def_id"), a.get("status")) for a in alerts)
    return json.dumps(sig, ensure_ascii=False, sort_keys=True)


def _ai_interpret_fallback(alerts, context):
    """AI 未設定時の決定論フォールバック。5 層を機械生成する。

    単一インスタンスは個別カード、2 件以上は横断パターン（④）を 1 枚に繋ぐ。
    """
    def _dur(sec):
        if sec is None:
            return "—"
        sec = abs(int(sec))
        d, h, m = sec // 86400, (sec % 86400) // 3600, (sec % 3600) // 60
        return f"{d}日" if d else (f"{h}時間" if h else f"{m}分")

    def _place(pid):
        if not pid:
            return "（未到達）"
        try:
            p = jobnet.NET["places"].get(pid)
            return p.get("name", pid) if p else pid
        except Exception:
            return pid

    def _one(a):
        status = a.get("status")
        overdue = status == "overdue"
        title = (f"■ 期限超過: {a.get('name') or a.get('def_id')}" if overdue
                 else f"▲ 逸脱: {a.get('name') or a.get('def_id')}")
        desc = (f"{a.get('target_value') or ''} は期待状態「{_place(a.get('expected_state'))}」"
                f"に未到達。期限 {a.get('deadline') or '—'}。")
        cause = (f"現在「{_place(a.get('actual_state'))}」に滞留 → 期待状態への遷移が未発火。"
                 if a.get("actual_state")
                 else "発火ログに到達記録なし → 起点トランジションが未発火かマーキング未配置。")
        if overdue:
            pred = f"期限を {_dur(a.get('overdue_seconds'))} 超過。放置すると超過が拡大。"
        else:
            pred = f"スラック {_dur(a.get('slack_seconds'))}。この速度では期限到達の余裕は限定的。"
        action = ("即時対応: 滞留状態の作動（Slack 送信等）を確認し、"
                  "未完了なら再実行または代替経路へ切替を検討。"
                  if overdue else
                  "猶予内だが進行を確認。次回の巡回で期待状態に到達できるか監視。")
        return {"title": title, "description": desc, "cause": cause,
                "prediction": pred, "pattern": "（単一インスタンス）", "action": action,
                "confidence": 0.4}

    if len(alerts) <= 1:
        return [_one(a) for a in alerts]

    # 複数 → 個別カード ＋ 横断パターン 1 枚
    cards = [_one(a) for a in alerts]
    names = "・".join((a.get("target_value") or a.get("name") or a.get("def_id")) for a in alerts)
    # 滞留状態の集計（横断で同じ状態に集中していれば系統的制約の兆候）
    from collections import Counter
    states = Counter(_place(a.get("actual_state")) for a in alerts if a.get("actual_state"))
    top_state, top_n = (states.most_common(1) + [("（分散）", 0)])[0]
    cards.append({
        "title": "◆ 横断パターン（AI 検知）",
        "description": f"{names} の {len(alerts)} 業務が同時期に逸脱・遅延中。",
        "cause": (f"{top_n} 件が「{top_state}」に集中 → 個別の遅延ではなく"
                  f"供給・資源側の系統的制約が疑われる。" if top_n >= 2
                  else "滞留状態は分散。個別要因の可能性が高い。"),
        "prediction": "制約が解消されない場合、集中している業務は相次いで期限超過へ。",
        "pattern": f"横断: {names} が同時期に逸脱（集中状態: {top_state}）",
        "action": "集中状態の資源（薬剤在庫・作業者・外部依存）を確認し、"
                  "代替・切替・優先度付けで横断的に解消する。",
        "confidence": 0.5,
    })
    return cards


def _ai_interpret_call(alerts, context):
    """生成 AI（Anthropic → ローカル LLM）に 5 層 JSON を生成させる。

    失敗時は None を返す（呼び出し側がフォールバックに落ちる）。
    """
    import claude_chat
    data = {
        "alerts": alerts,
        "firing_log": (context.get("firing_log") or [])[-10:],
        "sos_log": (context.get("sos_log") or [])[-10:],
        "inventory": context.get("inventory") or {},
    }
    user_msg = (
        "以下の運航データから、逸脱・遅延の異常を 5 層（記述・因果・予測・パターン・行動）で解読してください。\n"
        "JSON 配列のみを出力（各要素は {title, description, cause, prediction, pattern, action, confidence}）。"
        "confidence は 0〜1 の数値。横断パターンが検出できれば 1 枚追加すること。\n\n"
        + json.dumps(data, ensure_ascii=False)
    )
    anthropic = claude_chat._get_anthropic()
    api_key = claude_chat._get_api_key()
    if anthropic is not None and api_key:
        try:
            client = anthropic.Anthropic(api_key=api_key, base_url=claude_chat._get_base_url())
            resp = client.messages.create(
                model=claude_chat._get_model(),
                system=_AI_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=2048,
                temperature=0.3,
            )
            return resp.content[0].text
        except Exception as e:
            logger.exception("ai-interpret: Claude API error")
            return None
    if claude_chat._is_local_llm_available():
        try:
            return claude_chat._call_local_llm(
                [{"role": "user", "content": user_msg}],
                _AI_SYSTEM_PROMPT, [], {}, max_tokens=2048,
            )
        except Exception as e:
            logger.exception("ai-interpret: local LLM error")
            return None
    return None


def _extract_interpretations(text):
    """AI 出力から 5 層 JSON 配列を抽出（```json フェンス・前置き文に耐性）。"""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", t)
    if m:
        t = m.group(1)
    elif t.startswith("{"):
        t = "[" + t + "]"
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        arr = json.loads(t[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(arr, list) or not arr:
        return None
    keys = {"title", "description", "cause", "prediction", "pattern", "action", "confidence"}
    cleaned = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        item = {k: it.get(k, "") for k in
                ("title", "description", "cause", "prediction", "pattern", "action")}
        conf = it.get("confidence")
        try:
            item["confidence"] = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            item["confidence"] = 0.5
        cleaned.append(item)
    return cleaned or None


# ─── 役割（位置的角色）の解決 ────────────────────────────────────
# 要求の発生源は役割（人物でなく）（ts/真界_事業主体設計.md §2.6）。
# 役割→人の紐付け（delegation: role --[holds]--> worker）から現在保持者を解決し、
# 要求トークンに role / role_id / role_holder を載せる。
# モジュールレベル関数（Handler の外）で、テストが Handler をインスタンス化せず
# 直接呼べる。

def _role_context(conn, role_name: str = "事業主") -> dict:
    """役割（事業主等）の解決: {role, role_id, role_holder} を返す。

    roles を name で探し、entity.role_holder で保持者（worker 名）を解決する。
    役割が未登録なら {role: role_name, role_id: None, role_holder: None}。
    """
    r = conn.execute(
        "SELECT id, name FROM roles WHERE name=? ORDER BY id LIMIT 1", (role_name,)).fetchone()
    if not r:
        return {"role": role_name, "role_id": None, "role_holder": None}
    holder = entity.role_holder(conn, _SUBJECT_ID, _DOMAIN_ID, str(r["id"]))
    return {"role": r["name"], "role_id": r["id"],
            "role_holder": holder["name"] if holder else None}


# ─── 責任の帰還（役割保持者へ）— C層（§2.10・§11.4）─────────────
# 確認イベント（検鏡判定確定・定植完了・散布完了）を、その業務の
# responsible_role_id の保持者（worker）へ解決し、record_return で状態記録する。
# 責任は必ず人（役割保持者）で終わる（§2.7）。responsible_role_id が NULL なら
# 後方互換として subject の事業主（roles.name='事業主'）に解決。
# モジュールレベル関数（Handler の外）で、テストが直接呼べる。

# 確認イベント（work_type）→ 責務の正（schedule_def.name）への対応。
# 検鏡＝定植コホートの検鏡ステップ（同一 group_id の責務）・定植＝定植・散布＝防除。
_WORK_TYPE_DEF_NAME = {
    "inspection": "定植",
    "transplant": "定植",
    "spray": "防除",
}


def _responsible_role_id_for(conn, work_type: str):
    """確認イベントの責務担当役割（responsible_role_id）を解決する。

    work_type → schedule_def.name（_WORK_TYPE_DEF_NAME）→ responsible_role_id。
    未定義・NULL なら subject の事業主（roles.name='事業主'）にフォールバック。
    返り値は (role_id, role_name)（role_id は roles.id。解決不能なら (None, '事業主')）。
    """
    def_name = _WORK_TYPE_DEF_NAME.get(work_type)
    rid = None
    if def_name:
        d = conn.execute(
            "SELECT responsible_role_id FROM schedule_def WHERE name=? LIMIT 1",
            (def_name,)).fetchone()
        if d and d["responsible_role_id"]:
            rid = d["responsible_role_id"]
    if rid is None:
        r = conn.execute(
            "SELECT id FROM roles WHERE name='事業主' ORDER BY id LIMIT 1").fetchone()
        rid = str(r["id"]) if r else None
    if rid is None:
        return None, "事業主"
    rname = conn.execute("SELECT name FROM roles WHERE id=?", (rid,)).fetchone()
    return rid, (rname["name"] if rname else "事業主")


def _record_accountability(conn, work_type: str, ref_id, detail: str):
    """確認イベントの責任を役割保持者（worker）へ record_return（C層・§2.10）。

    責務担当役割（responsible_role_id、NULL=事業主）の保持者 worker を解決し、
    その worker エンティティに状態記録する（責任は必ず人で終わる）。帰還は
    ①状態記録のみ（token_key=None）— 確認はマーキングを DB 導出で決めるため
    STN は再開しない。解決不能（役割・保持者なし）なら None を返し記録しない。
    """
    role_id, role_name = _responsible_role_id_for(conn, work_type)
    if role_id is None:
        return None
    holder = entity.role_holder(conn, _SUBJECT_ID, _DOMAIN_ID, str(role_id))
    if not holder:
        return None
    import humos
    humos.record_return(
        _SUBJECT_ID, _DOMAIN_ID, "worker", str(holder["object_id"]),
        attr_key=f"accountability_{work_type}_{ref_id}",
        attr_value={"role": role_name, "detail": detail,
                    "confirmed_at": datetime.datetime.utcnow().isoformat()},
        source="role_holder")
    return {"role": role_name, "holder": holder["name"],
            "worker_object_id": str(holder["object_id"])}


# ─── Handlers ───────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        # Allow same-origin fetch from any origin (dev/local network)
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ──────────────────────────────────────────────────────

    def do_GET(self):
        m = re.match(r"^/api/spray_schedule/(\d+)/place$", self.path)
        if m:
            self._handle_spray_schedule_place(m.group(1))
            return

        # 境界言語の形式知（rbp_*）CRUD（🧩 知識メンテ）
        # /api/rbp/tasks = タスク（業務）一覧＋接地状態（5段の有無・ライブ判定）
        if self.path.split("?")[0] == "/api/rbp/tasks":
            self._handle_rbp_tasks_get()
            return
        # /api/rbp/tasks/<task_id>/knowledge = タスクの5段ナレッジ（認知/評価/決定/投射/作動）
        # を rbp_* の行として返す。ペトリネットの T ノードクリックで5段モデルを表示し、
        # 各段の「ナレッジ」クリックでこの段の実際の知識行を見せるのに使う（§12.7）。
        if self.path.startswith("/api/rbp/tasks/") and self.path.endswith("/knowledge"):
            self._handle_rbp_task_knowledge()
            return
        if self.path.startswith("/api/rbp/"):
            self._handle_rbp_get()
            return

        if self.path.split("?")[0] == "/api/net":
            # 状態遷移ネット（プレース↔トランジション構造）の視覚化データ。
            # 正=jobnet.py。designer がこれを取得して Petri 網を描画する。
            # ?cohort_id=N → コホートごとのネット（検鏡鎖＋定植AND-JOIN）。
            self._handle_net_get()
            return

        if self.path == "/api/humos":
            # HUMOS（土壌）の記録ログ: 現在のマーキング・発火ログ・作動ログ。
            # 正=humos.py（DB 永続・共有 OS humos_os へのラッパー）。designer の「HUMOS ログ」パネルが取得。
            self._handle_humos_get()
            return

        if self.path == "/api/humos/meta":
            # HUMOS OS コンソール（humos.html）のメタ情報（ドメイン非依存）。
            # 不変（OS バージョン・モジュール）vs 可変（entity/RBP/schedule 数）。
            self._handle_humos_meta_get()
            return

        # 運航管理センター（operations）— PLAN × 実状態 × 現在時刻 の突き合わせ
        if self.path.startswith("/api/operations/"):
            if self.path == "/api/operations/status":
                self._handle_operations_status()
            elif self.path == "/api/operations/alerts":
                self._handle_operations_alerts()
            elif self.path == "/api/operations/timeline":
                self._handle_operations_timeline()
            elif self.path == "/api/operations/defs" or \
                    self.path.startswith("/api/operations/defs?"):
                self._handle_operations_defs_get()
            else:
                self._send_json(404, {"error": "unknown operations endpoint"})
            return

        if self.path == "/api/pesticides":
            conn = get_db()
            rows = conn.execute("SELECT * FROM pesticides ORDER BY id").fetchall()
            conn.close()
            result = []
            for r in rows:
                d = dict(r)
                # Parse JSON-string columns back to arrays
                for col in ("targetVector", "targetNames", "mixingBanTargets"):
                    if d.get(col) and isinstance(d[col], str):
                        try:
                            d[col] = json.loads(d[col])
                        except (json.JSONDecodeError, TypeError):
                            pass
                result.append(d)
            self._send_json(200, {"pesticides": result})
            return

        if self.path.startswith("/api/pesticides/"):
            drug_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM pesticides WHERE id=?", (drug_id,)).fetchone()
            conn.close()
            if row:
                d = dict(row)
                for col in ("targetVector", "targetNames", "mixingBanTargets"):
                    if d.get(col) and isinstance(d[col], str):
                        try:
                            d[col] = json.loads(d[col])
                        except (json.JSONDecodeError, TypeError):
                            pass
                self._send_json(200, d)
            else:
                self._send_json(404, {"error": f"pesticide {drug_id} not found"})
            return

        if self.path == "/api/diseases":
            conn = get_db()
            rows = conn.execute("SELECT * FROM diseases ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"diseases": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/diseases/"):
            disease_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM diseases WHERE id=?", (int(disease_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"disease {disease_id} not found"})
            return

        if self.path == "/api/fields":
            conn = get_db()
            rows = conn.execute("SELECT * FROM fields ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"fields": [dict(r) for r in rows]})
            return

        # エンティティ層（v1.2.0）— 物理界の実在（3-level ID: domain_id⊃entity_id⊃object_id）。
        # 圃場・薬剤・従業員のオブジェクト＋属性＋構成品を返す。?domain_id=stb（既定 stb）。
        if self.path.split("?")[0] == "/api/entity":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
            # 4-level ID: subject_id ⊃ domain_id。legacy shim（?domain_id=stb → mikakura/iichigo）。
            subject_id = params.get("subject_id", "mikakura")
            domain_id = params.get("domain_id", "iichigo")
            if domain_id == "stb" and "subject_id" not in params:
                subject_id, domain_id = "mikakura", "iichigo"
            import json as _json
            def _j(s):
                try: return _json.loads(s) if s else {}
                except Exception: return {}
            conn = get_db()
            objs = conn.execute(
                "SELECT * FROM entity WHERE subject_id=? AND domain_id=? "
                "ORDER BY entity_id, object_id", (subject_id, domain_id)).fetchall()
            comps = conn.execute(
                "SELECT * FROM object_component WHERE subject_id=? AND domain_id=? "
                "ORDER BY entity_id, object_id, comp_type, comp_id",
                (subject_id, domain_id)).fetchall()
            conn.close()
            ent = {}
            for o in objs:
                d = dict(o)
                d["attrs"] = _j(d.get("attrs"))
                d["components"] = []
                ent.setdefault(d["entity_id"], []).append(d)
            for c in comps:
                d = dict(c)
                d["attrs"] = _j(d.get("attrs"))
                for obj in ent.get(d["entity_id"], []):
                    if obj["object_id"] == d["object_id"]:
                        obj["components"].append(d)
            self._send_json(200, {"subject_id": subject_id, "domain_id": domain_id,
                                  "entities": [{"entity_id": k, "objects": v}
                                               for k, v in ent.items()]})
            return

        # 役割（位置的角色）— 主体が持つ役割と保持者（delegation: role --[holds]--> worker）。
        # 要求の発生源は役割（人物でなく）（ts/真界_事業主体設計.md §2.6）。
        if self.path.split("?")[0] == "/api/roles":
            from humos_os import entity as _entity
            conn = get_db()
            roles = []
            for r in _entity.list_roles(conn, _SUBJECT_ID, _DOMAIN_ID):
                role = r["role"]
                holder = r["holder"]
                roles.append({
                    "role_id": role["object_id"] if role else None,
                    "name": role["name"] if role else None,
                    "holder_id": holder["object_id"] if holder else None,
                    "holder_name": holder["name"] if holder else None,
                })
            conn.close()
            self._send_json(200, {"subject_id": _SUBJECT_ID, "domain_id": _DOMAIN_ID,
                                  "roles": roles})
            return

        if self.path.startswith("/api/fields/"):
            field_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM fields WHERE id=?", (int(field_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"field {field_id} not found"})
            return

        # ベッド台帳（beds）— 圃場の関連テーブル（本舗の構成品・一列ずつの長さ）。
        # マルチ選定の認知/要求評価の緒言（要求ベクトルの軸）。
        if self.path.split("?")[0] == "/api/beds":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            field_id = (dict(x.split("=", 1) for x in q.split("&") if "=" in x)).get("field_id")
            conn = get_db()
            if field_id:
                rows = conn.execute(
                    "SELECT * FROM beds WHERE field_id=? ORDER BY seq, id", (int(field_id),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM beds ORDER BY field_id, seq, id").fetchall()
            conn.close()
            self._send_json(200, {"beds": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/beds/"):
            bed_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM beds WHERE id=?", (int(bed_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"bed {bed_id} not found"})
            return

        # マルチ資材DB（mulch_materials）— マルチ選定の仕様決定の緒言（候補プール）。
        if self.path == "/api/mulch_materials":
            conn = get_db()
            rows = conn.execute("SELECT * FROM mulch_materials ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"materials": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/mulch_materials/"):
            mat_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM mulch_materials WHERE id=?", (int(mat_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"mulch_material {mat_id} not found"})
            return

        # マルチ在庫（mulch_inventory）— マルチ在庫確認の緒言（数量型）。
        if self.path.split("?")[0] == "/api/mulch_inventory":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
            conn = get_db()
            if params.get("material_id"):
                rows = conn.execute(
                    "SELECT * FROM mulch_inventory WHERE material_id=? ORDER BY id",
                    (int(params["material_id"]),)).fetchall()
            elif params.get("field_id"):
                rows = conn.execute(
                    "SELECT * FROM mulch_inventory WHERE field_id=? ORDER BY id",
                    (int(params["field_id"]),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM mulch_inventory ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"inventory": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/mulch_inventory/"):
            inv_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM mulch_inventory WHERE id=?", (int(inv_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"mulch_inventory {inv_id} not found"})
            return

        # マルチ敷設手順書の合成導出（投射・§12.7）。
        if self.path.split("?")[0] == "/api/mulch/projection":
            self._handle_mulch_projection()
            return

        # 検鏡場所マスター（inspection_locations）— 花芽分化の顕微鏡検査場所。
        if self.path == "/api/inspection_locations":
            conn = get_db()
            rows = conn.execute("SELECT * FROM inspection_locations ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"locations": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/inspection_locations/"):
            loc_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM inspection_locations WHERE id=?", (int(loc_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"inspection_location {loc_id} not found"})
            return

        # 従業者マスター（workers）— 全作業の「誰」。
        if self.path == "/api/workers":
            conn = get_db()
            rows = conn.execute("SELECT * FROM workers ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"workers": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/workers/"):
            w_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM workers WHERE id=?", (int(w_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"worker {w_id} not found"})
            return

        # コホート（育苗バッチ）マスター。
        if self.path == "/api/cohorts":
            conn = get_db()
            rows = conn.execute("SELECT * FROM cohorts ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"cohorts": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/cohorts/"):
            c_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM cohorts WHERE id=?", (int(c_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"cohort {c_id} not found"})
            return

        # 定植予定（transplant_plan）— コホートの定植。?cohort_id= でフィルタ可。
        if self.path == "/api/transplant_plan" or self.path.startswith("/api/transplant_plan?"):
            cohort_id = None
            if "?" in self.path:
                for kv in self.path.split("?", 1)[1].split("&"):
                    if kv.startswith("cohort_id="):
                        cohort_id = kv.split("=", 1)[1]
            conn = get_db()
            if cohort_id:
                rows = conn.execute(
                    "SELECT * FROM transplant_plan WHERE cohort_id=? ORDER BY id",
                    (int(cohort_id),)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM transplant_plan ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
            return

        # 熱消毒予定（heat_schedule）— コホートのベッド準備（熱消毒）の着手日・期限日・実績。
        if self.path == "/api/heat_schedule" or self.path.startswith("/api/heat_schedule?"):
            cohort_id = None
            if "?" in self.path:
                for kv in self.path.split("?", 1)[1].split("&"):
                    if kv.startswith("cohort_id="):
                        cohort_id = kv.split("=", 1)[1]
            conn = get_db()
            if cohort_id:
                rows = conn.execute(
                    "SELECT * FROM heat_schedule WHERE cohort_id=? ORDER BY id",
                    (int(cohort_id),)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM heat_schedule ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
            return

        if self.path == "/api/eval-boxes/custom":
            conn = get_db()
            rows = conn.execute("SELECT * FROM eval_boxes_custom ORDER BY id").fetchall()
            conn.close()
            result = {}
            for r in rows:
                result[r["id"]] = {"name": r["name"], "vector": json.loads(r["vector"])}
            self._send_json(200, result)
            return

        if self.path == "/api/spray_history":
            conn = get_db()
            rows = conn.execute("SELECT * FROM spray_history ORDER BY date").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
            return

        if self.path == "/api/spray_schedule" or self.path.startswith("/api/spray_schedule?"):
            year = None
            if "?" in self.path:
                for kv in self.path.split("?", 1)[1].split("&"):
                    if kv.startswith("year="):
                        year = kv.split("=", 1)[1]
            conn = get_db()
            if year:
                rows = conn.execute(
                    "SELECT * FROM spray_schedule WHERE schedule_date LIKE ? ORDER BY schedule_date",
                    (f"{year}-%",),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM spray_schedule ORDER BY schedule_date").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
            return

        # 定植暦（transplant_schedule）— 花芽分化確認の予定・外部判定記録。
        if self.path == "/api/transplant_schedule" or self.path.startswith("/api/transplant_schedule?"):
            year = None
            if "?" in self.path:
                for kv in self.path.split("?", 1)[1].split("&"):
                    if kv.startswith("year="):
                        year = kv.split("=", 1)[1]
            conn = get_db()
            if year:
                rows = conn.execute(
                    "SELECT * FROM transplant_schedule WHERE schedule_date LIKE ? ORDER BY schedule_date",
                    (f"{year}-%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM transplant_schedule ORDER BY schedule_date").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/inventory"):
            self._handle_inventory_get()
            return

        if self.path == "/chat" or self.path == "/chat/":
            self._serve_chat_page()
            return

        if self.path == "/designer" or self.path == "/designer/":
            self._serve_designer_page()
            return

        if self.path == "/viz" or self.path == "/viz/":
            # 境界言語図表（rbp_viz が生DBから生成した自己完結HTML）。
            # 生DB（rbp_* 知識DB）から動的生成するため、🧩知識メンテUIでの編集を反映。
            self._serve_viz_page()
            return

        _route = self.path.split("?")[0]  # クエリ剥がし（/knowledge?table=... 等）
        if _route == "/knowledge" or _route == "/knowledge/":
            # 🧩 境界言語知識メンテナンス（rbp_* CRUD）— index.html から分離した独立ページ。
            # ?table=<rbp_table> でテーブル事前選択（ペトリネットUI のナレッジ編集導線）。
            self._serve_knowledge_page()
            return

        if _route == "/operations" or _route == "/operations/":
            # 業務運航管理センター（管制塔 UI）— ①運行図表 ②ペトリネット ③異常解釈 ④フィード。
            self._serve_operations_page()
            return

        if _route == "/stn" or _route == "/stn/":
            # STN・ライブ（/operations パネル②の独立ページ）— ペトリネット×ドメイン×膜（接地）。
            self._serve_stn_page()
            return

        if _route == "/humos" or _route == "/humos/":
            # HUMOS OS コンソール（真界管理画面）— 本質4層（①本質②核心③機構④OSである所以）。
            # ドメイン非依存: 各サーバーが同一 UI を配信し、そのサーバーの humos_os DB を読む。
            self._serve_humos_page()
            return

        if self.path == "/api/tokens/check":
            self._handle_tokens_check()
            return

        # Non-API: serve static files via parent class
        super().do_GET()

    # ── PUT ──────────────────────────────────────────────────────

    def do_PUT(self):
        if self.path.startswith("/api/rbp/"):
            self._handle_rbp_put()
            return

        # 構成品（object_component・v1.4.0）の属性更新。
        # 値の正は HUMOS entity 層（プランタン/育苗棚は HUMOS 固有）。
        # body: {subject_id, domain_id, entity_id, object_id, comp_type, comp_id, attrs}
        if self.path == "/api/entity/component":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            required = ("subject_id", "domain_id", "entity_id", "object_id",
                        "comp_type", "comp_id")
            if any(k not in body for k in required):
                self._send_json(400, {"error": f"missing: {required}"})
                return
            from humos_os import entity
            conn = get_db()
            entity.ensure_entity_schema(conn)
            entity.upsert_component(conn, body["subject_id"], body["domain_id"],
                                    body["entity_id"], body["object_id"],
                                    body["comp_type"], body["comp_id"],
                                    name=body.get("name"), attrs=body.get("attrs"))
            conn.close()
            self._send_json(200, {"status": "updated",
                                  "comp_id": body["comp_id"]})
            return

        if self.path.startswith("/api/pesticides/"):
            drug_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM pesticides WHERE id=?", (drug_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"pesticide {drug_id} not found"})
                return

            # Merge: keep existing fields, overwrite provided ones
            merged = dict(row)
            merged.update(body)
            merged["id"] = drug_id

            conn.execute(
                """UPDATE pesticides SET name=?, activeIngredient=?, category=?,
                   targetVector=?, targetNames=?, phiDays=?, mixingRestriction=?,
                   mixingBanTargets=?, maxApplications=?, toxicityClass=?,
                   system=?, systemCode=?, dilutionRate=?,
                   brand=?, formulation=?, packaging=?, contentForm=?,
                   applicationRate=?, capacity=?, packSize=?, packUnit=?, price=?
                   WHERE id=?""",
                (
                    merged["name"],
                    merged.get("activeIngredient"),
                    merged.get("category"),
                    json.dumps(merged.get("targetVector", [])),
                    json.dumps(merged.get("targetNames", [])),
                    merged.get("phiDays"),
                    merged.get("mixingRestriction"),
                    json.dumps(merged.get("mixingBanTargets", [])),
                    merged.get("maxApplications"),
                    merged.get("toxicityClass"),
                    merged.get("system"),
                    merged.get("systemCode"),
                    merged.get("dilutionRate"),
                    merged.get("brand"),
                    merged.get("formulation"),
                    merged.get("packaging"),
                    merged.get("contentForm"),
                    merged.get("applicationRate"),
                    merged.get("capacity"),
                    merged.get("packSize"),
                    merged.get("packUnit"),
                    merged.get("price"),
                    drug_id,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": drug_id})
            return

        if self.path.startswith("/api/diseases/"):
            disease_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM diseases WHERE id=?", (int(disease_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"disease {disease_id} not found"})
                return

            merged = dict(row)
            merged.update(body)
            merged["id"] = int(disease_id)

            conn.execute(
                "UPDATE diseases SET name=?, type=? WHERE id=?",
                (merged["name"], merged["type"], merged["id"]),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": disease_id})
            return

        if self.path.startswith("/api/fields/"):
            field_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM fields WHERE id=?", (int(field_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"field {field_id} not found"})
                return

            new_type = body.get("type", row["type"])
            if new_type not in ("nursery", "main"):
                conn.close()
                self._send_json(400, {"error": "type must be 'nursery' or 'main'"})
                return
            new_unit = body.get("area_unit", row["area_unit"])
            if new_unit is not None and new_unit not in ("m2", "tan", "tsubo"):
                conn.close()
                self._send_json(400, {"error": "area_unit must be one of m2/tan/tsubo or null"})
                return
            new_spray_rate = body.get("spray_rate", row["spray_rate"])
            new_spray_volume = body.get("spray_volume", row["spray_volume"])
            if new_spray_rate is not None:
                try:
                    new_spray_rate = float(new_spray_rate)
                except (TypeError, ValueError):
                    conn.close()
                    self._send_json(400, {"error": "spray_rate must be a number or null"})
                    return
            if new_spray_volume is not None:
                try:
                    new_spray_volume = float(new_spray_volume)
                except (TypeError, ValueError):
                    conn.close()
                    self._send_json(400, {"error": "spray_volume must be a number or null"})
                    return

            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                """UPDATE fields SET name=?, type=?, area_value=?, area_unit=?,
                   spray_rate=?, spray_volume=?, updated_at=?
                   WHERE id=?""",
                (
                    body.get("name", row["name"]),
                    new_type,
                    body.get("area_value", row["area_value"]),
                    new_unit,
                    new_spray_rate,
                    new_spray_volume,
                    now,
                    int(field_id),
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": field_id})
            return

        # ベッド台帳（beds）の更新。
        if self.path.startswith("/api/beds/"):
            bed_id = int(self.path.split("/")[-1])
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute("SELECT * FROM beds WHERE id=?", (bed_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"bed {bed_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                """UPDATE beds SET field_id=?, name=?, seq=?, length_m=?, width_m=?,
                   mulch_type=?, notes=?, updated_at=? WHERE id=?""",
                (
                    body.get("field_id", row["field_id"]),
                    body.get("name", row["name"]),
                    body.get("seq", row["seq"]),
                    body.get("length_m", row["length_m"]),
                    body.get("width_m", row["width_m"]),
                    body.get("mulch_type", row["mulch_type"]),
                    body.get("notes", row["notes"]),
                    now,
                    bed_id,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": bed_id})
            return

        # マルチ資材DB（mulch_materials）の更新。
        if self.path.startswith("/api/mulch_materials/"):
            mat_id = int(self.path.split("/")[-1])
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute("SELECT * FROM mulch_materials WHERE id=?", (mat_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"mulch_material {mat_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                """UPDATE mulch_materials SET name=?, type=?, width_cm=?, hole_interval_cm=?,
                   length_cm=?, notes=?, updated_at=? WHERE id=?""",
                (
                    body.get("name", row["name"]),
                    body.get("type", row["type"]),
                    body.get("width_cm", row["width_cm"]),
                    body.get("hole_interval_cm", row["hole_interval_cm"]),
                    body.get("length_cm", row["length_cm"]),
                    body.get("notes", row["notes"]),
                    now,
                    mat_id,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": mat_id})
            return

        # マルチ在庫（mulch_inventory）の更新。
        if self.path.startswith("/api/mulch_inventory/"):
            inv_id = int(self.path.split("/")[-1])
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute("SELECT * FROM mulch_inventory WHERE id=?", (inv_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"mulch_inventory {inv_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                """UPDATE mulch_inventory SET material_id=?, field_id=?, quantity=?, unit=?,
                   lot_number=?, expiry_date=?, notes=?, updated_at=? WHERE id=?""",
                (
                    body.get("material_id", row["material_id"]),
                    body.get("field_id", row["field_id"]),
                    body.get("quantity", row["quantity"]),
                    body.get("unit", row["unit"]),
                    body.get("lot_number", row["lot_number"]),
                    body.get("expiry_date", row["expiry_date"]),
                    body.get("notes", row["notes"]),
                    now,
                    inv_id,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": inv_id})
            return

        # 検鏡場所マスターの更新（名前変更）。
        if self.path.startswith("/api/inspection_locations/"):
            loc_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM inspection_locations WHERE id=?", (int(loc_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"inspection_location {loc_id} not found"})
                return
            new_name = (body.get("name") or "").strip()
            if not new_name:
                conn.close()
                self._send_json(400, {"error": "missing field: name"})
                return
            dup = conn.execute(
                "SELECT id FROM inspection_locations WHERE name=? AND id<>?",
                (new_name, int(loc_id))).fetchone()
            if dup:
                conn.close()
                self._send_json(409, {"error": f"location '{new_name}' already exists"})
                return
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE inspection_locations SET name=?, updated_at=? WHERE id=?",
                (new_name, now, int(loc_id)))
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": loc_id})
            return

        # 従業者マスター（workers）更新。
        # 役割の保持者変更（世代交代）— body {"worker_id": N}。
        # delegation の holds エッジを差し替える（entity.assign_role）。役割ノードは不変。
        if self.path.startswith("/api/roles/"):
            r_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            worker_id = body.get("worker_id")
            if worker_id is None:
                self._send_json(400, {"error": "missing field: worker_id"})
                return
            from humos_os import entity as _entity
            conn = get_db()
            role = conn.execute("SELECT id FROM roles WHERE id=?", (int(r_id),)).fetchone()
            if role is None:
                conn.close()
                self._send_json(404, {"error": f"role {r_id} not found"})
                return
            worker = conn.execute("SELECT id, name FROM workers WHERE id=?",
                                  (int(worker_id),)).fetchone()
            if worker is None:
                conn.close()
                self._send_json(404, {"error": f"worker {worker_id} not found"})
                return
            _entity.assign_role(conn, _SUBJECT_ID, _DOMAIN_ID, str(role["id"]),
                                str(worker["id"]))
            conn.close()
            self._send_json(200, {"status": "updated", "role_id": r_id,
                                  "holder_id": str(worker["id"]),
                                  "holder_name": worker["name"]})
            return

        if self.path.startswith("/api/workers/"):
            w_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute("SELECT * FROM workers WHERE id=?", (int(w_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"worker {w_id} not found"})
                return
            new_name = (body.get("name") or row["name"]).strip()
            if not new_name:
                conn.close()
                self._send_json(400, {"error": "missing field: name"})
                return
            dup = conn.execute(
                "SELECT id FROM workers WHERE name=? AND id<>?", (new_name, int(w_id))).fetchone()
            if dup:
                conn.close()
                self._send_json(409, {"error": f"worker '{new_name}' already exists"})
                return
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE workers SET name=?, role=?, phone=?, org=?, updated_at=? WHERE id=?",
                (new_name, body.get("role", row["role"]), body.get("phone", row["phone"]),
                 body.get("org", row["org"]), now, int(w_id)))
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": w_id})
            return

        # コホート（育苗バッチ）マスター更新。
        if self.path.startswith("/api/cohorts/"):
            c_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute("SELECT * FROM cohorts WHERE id=?", (int(c_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"cohort {c_id} not found"})
                return
            new_name = (body.get("name") or row["name"]).strip()
            if not new_name:
                conn.close()
                self._send_json(400, {"error": "missing field: name"})
                return
            dup = conn.execute(
                "SELECT id FROM cohorts WHERE name=? AND id<>?", (new_name, int(c_id))).fetchone()
            if dup:
                conn.close()
                self._send_json(409, {"error": f"cohort '{new_name}' already exists"})
                return
            # 第N期・収穫年・育苗開始年月（未指定なら既存値を保持）。
            def _int_or(key, cur):
                v = body.get(key, cur)
                try:
                    return int(v) if v not in (None, "") else None
                except (TypeError, ValueError):
                    return None
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE cohorts SET name=?, period_no=?, harvest_year=?, seedling_start=?, "
                "field_id=?, notes=?, updated_at=? WHERE id=?",
                (new_name, _int_or("period_no", row["period_no"]),
                 _int_or("harvest_year", row["harvest_year"]),
                 body.get("seedling_start", row["seedling_start"]),
                 body.get("field_id", row["field_id"]),
                 body.get("notes", row["notes"]), now, int(c_id)))
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": c_id})
            return

        # 定植予定（transplant_plan）更新（status=done 完了記録・assignee・手動日付上書き）。
        if self.path.startswith("/api/transplant_plan/"):
            tp_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM transplant_plan WHERE id=?", (int(tp_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"transplant_plan {tp_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            _aids = _assignee_ids_from_body(body) if "assignee_ids" in body else _assignee_ids_from_row(row)
            _single = _aids[0] if _aids else (body.get("assignee_id", row["assignee_id"]))
            conn.execute(
                """UPDATE transplant_plan SET
                   schedule_date=?, assignee_id=?, assignee_ids=?, status=?, notes=?, updated_at=?
                   WHERE id=?""",
                (
                    body.get("schedule_date", row["schedule_date"]),
                    _single,
                    json.dumps(_aids),
                    body.get("status", row["status"]),
                    body.get("notes", row["notes"]),
                    now,
                    int(tp_id),
                ),
            )
            conn.commit()
            # 責任の帰還（C層・§2.10）— 定植完了（status=done）を責務担当役割の
            # 保持者（worker）へ。責任は必ず人で終わる。
            if (body.get("status", row["status"]) or "") == "done":
                try:
                    _record_accountability(conn, "transplant", tp_id, "定植完了")
                except Exception as e:  # noqa: BLE001 — 帰還失敗は更新自体を阻害しない
                    logger.warning(f"責任帰還失敗（定植 {tp_id}）: {e}")
            conn.close()
            self._send_json(200, {"status": "updated", "id": tp_id})
            return

        # 熱消毒予定（heat_schedule）更新（status=done 完了記録・assignee・手動日付上書き）。
        if self.path.startswith("/api/heat_schedule/"):
            hs_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM heat_schedule WHERE id=?", (int(hs_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"heat_schedule {hs_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            _aids = _assignee_ids_from_body(body) if "assignee_ids" in body else _assignee_ids_from_row(row)
            _single = _aids[0] if _aids else (body.get("assignee_id", row["assignee_id"]))
            conn.execute(
                """UPDATE heat_schedule SET
                   start_date=?, deadline_date=?, assignee_id=?, assignee_ids=?, status=?, notes=?, updated_at=?
                   WHERE id=?""",
                (
                    body.get("start_date", row["start_date"]),
                    body.get("deadline_date", row["deadline_date"]),
                    _single,
                    json.dumps(_aids),
                    body.get("status", row["status"]),
                    body.get("notes", row["notes"]),
                    now,
                    int(hs_id),
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": hs_id})
            return

        if self.path.startswith("/api/spray_schedule/"):
            sched_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM spray_schedule WHERE id=?", (sched_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"spray_schedule {sched_id} not found"})
                return

            now = datetime.datetime.utcnow().isoformat()
            _aids = _assignee_ids_from_body(body) if "assignee_ids" in body else _assignee_ids_from_row(row)
            conn.execute(
                """UPDATE spray_schedule SET
                   schedule_date=?, actual_date=?, status=?, trigger_type=?, trigger_ref=?,
                   eval_box_id=?, rb_out_json=?, set_ids=?, pesticide_ids=?, operator=?,
                   assignee_ids=?, weather=?, notes=?, field_id=?, updated_at=?
                   WHERE id=?""",
                (
                    body.get("schedule_date", row["schedule_date"]),
                    body.get("actual_date", row["actual_date"]),
                    body.get("status", row["status"]),
                    body.get("trigger_type", row["trigger_type"]),
                    body.get("trigger_ref", row["trigger_ref"]),
                    body.get("eval_box_id", row["eval_box_id"]),
                    json.dumps(body["rb_out_json"]) if body.get("rb_out_json") is not None else (row["rb_out_json"] if "rb_out_json" not in body else None),
                    json.dumps(body["set_ids"]) if "set_ids" in body else row["set_ids"],
                    json.dumps(body["pesticide_ids"]) if "pesticide_ids" in body else row["pesticide_ids"],
                    body.get("operator", row["operator"]),
                    json.dumps(_aids),
                    body.get("weather", row["weather"]),
                    body.get("notes", row["notes"]),
                    body.get("field_id", row["field_id"]),
                    now,
                    sched_id,
                ),
            )
            conn.commit()
            # 責任の帰還（C層・§2.10）— 散布完了（status=done）を責務担当役割の
            # 保持者（worker）へ。責任は必ず人で終わる。
            if (body.get("status", row["status"]) or "") == "done":
                try:
                    _record_accountability(conn, "spray", sched_id, "散布完了")
                except Exception as e:  # noqa: BLE001 — 帰還失敗は更新自体を阻害しない
                    logger.warning(f"責任帰還失敗（散布 {sched_id}）: {e}")
            conn.close()
            self._send_json(200, {"status": "updated", "id": sched_id})
            return

        # 定植暦の更新（日程・備考・圃場・外部判定記録）。
        if self.path.startswith("/api/transplant_schedule/"):
            t_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM transplant_schedule WHERE id=?", (t_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"transplant_schedule {t_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            # 担当は複数（assignee_ids）が正。単一 assignee_id は後方互換（先頭）。
            _aids = _assignee_ids_from_body(body) if "assignee_ids" in body else _assignee_ids_from_row(row)
            _single = _aids[0] if _aids else (body.get("assignee_id", row["assignee_id"]))
            conn.execute(
                """UPDATE transplant_schedule SET
                   schedule_date=?, start_time=?, end_time=?, judge_date=?, judge_result=?,
                   judge_org=?, judge_location=?, status=?, notes=?, field_id=?,
                   cohort_id=?, seq=?, is_final=?, assignee_id=?, assignee_ids=?, updated_at=?
                   WHERE id=?""",
                (
                    body.get("schedule_date", row["schedule_date"]),
                    body.get("start_time", row["start_time"]),
                    body.get("end_time", row["end_time"]),
                    body.get("judge_date", row["judge_date"]),
                    body.get("judge_result", row["judge_result"]),
                    body.get("judge_org", row["judge_org"]),
                    body.get("judge_location", row["judge_location"]),
                    body.get("status", row["status"]),
                    body.get("notes", row["notes"]),
                    body.get("field_id", row["field_id"]),
                    body.get("cohort_id", row["cohort_id"]),
                    body.get("seq", row["seq"]),
                    int(body.get("is_final", row["is_final"]) or 0),
                    _single,
                    json.dumps(_aids),
                    now,
                    t_id,
                ),
            )
            conn.commit()
            # 帰還（§2.4・帰還膜）— 外部判定組織（那賀地方いちご生産組合連合会）の
            # 判定がドメインエンティティに戻る。判定が確定（judge_result あり or
            # status=confirmed）したとき、判定組織の state に記録（record_return ①）。
            # 帰還膜は「主体→プレース」でなく「外部組織→判定プレース（P_judge{seq}）」
            # の新しい膜種（ts/真界_事業主体設計.md §7）。コホートネットの P_judge{seq}
            # マーキングは DB 導出（本行）が正であり、record_return は判定組織への
            # 帰還（状態記録・監査）として系をドメインレベルで閉じる。
            _judge_org_name = body.get("judge_org", row["judge_org"])
            _judged_now = bool(body.get("judge_result")) or \
                (body.get("status") or row["status"]) == "confirmed"
            if _judged_now and _judge_org_name:
                _org = conn.execute(
                    "SELECT id FROM organizations WHERE name=?",
                    (_judge_org_name,)).fetchone()
                if _org:
                    import humos
                    try:
                        humos.record_return(
                            "mikakura", "iichigo", "organization", str(_org[0]),
                            attr_key=f"judge_result_{t_id}",
                            attr_value=body.get("judge_result") or row["judge_result"],
                            token_key=None, source="judge_org")
                    except Exception as e:  # noqa: BLE001 — 帰還失敗は更新自体を阻害しない
                        logger.warning(f"帰還記録失敗（検鏡 {t_id}）: {e}")
            # 責任の帰還（C層・§2.10）— 検鏡判定確定を責務担当役割の保持者（worker）へ。
            # 判定組織への帰還（上）は「外部の判定が帰還」、こちらは「確認で責任を負う
            # 役割保持者」への帰還（責任は必ず人で終わる）。
            if _judged_now:
                try:
                    _record_accountability(
                        conn, "inspection", t_id,
                        f"検鏡判定確定 {body.get('judge_result') or row['judge_result']}")
                except Exception as e:  # noqa: BLE001 — 帰還失敗は更新自体を阻害しない
                    logger.warning(f"責任帰還失敗（検鏡 {t_id}）: {e}")
            conn.close()
            self._send_json(200, {"status": "updated", "id": t_id})
            return

        if self.path.startswith("/api/inventory/"):
            self._handle_inventory_put()
            return

        self._send_json(404, {"error": "not found"})
        return

    # ── DELETE ───────────────────────────────────────────────────

    def do_DELETE(self):
        if self.path.startswith("/api/rbp/"):
            self._handle_rbp_delete()
            return

        if self.path.startswith("/api/pesticides/"):
            drug_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM pesticides WHERE id=?", (drug_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"pesticide {drug_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": drug_id})
            return

        if self.path.startswith("/api/diseases/"):
            disease_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM diseases WHERE id=?", (int(disease_id),))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"disease {disease_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": disease_id})
            return

        if self.path.startswith("/api/fields/"):
            field_id = self.path.split("/")[-1]
            field_id = int(field_id)
            conn = get_db()
            # 参照する子行（防除暦/防除履歴/在庫）を先に NULL 化してから削除。
            # PRAGMA foreign_keys=ON のため、トランザクション内で一気に行う。
            try:
                with conn:
                    conn.execute("UPDATE spray_schedule SET field_id=NULL WHERE field_id=?", (field_id,))
                    conn.execute("UPDATE spray_history  SET field_id=NULL WHERE field_id=?", (field_id,))
                    conn.execute("UPDATE inventory      SET field_id=NULL WHERE field_id=?", (field_id,))
                    conn.execute("UPDATE mulch_inventory SET field_id=NULL WHERE field_id=?", (field_id,))
                    conn.execute("DELETE FROM beds      WHERE field_id=?", (field_id,))  # 関連テーブル（本舗の構成品）
                    cur = conn.execute("DELETE FROM fields WHERE id=?", (field_id,))
            except sqlite3.Error:
                conn.close()
                self._send_json(500, {"error": "failed to delete field"})
                return
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"field {field_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": field_id})
            return

        # ベッド台帳（beds）の削除。
        if self.path.startswith("/api/beds/"):
            bed_id = int(self.path.split("/")[-1])
            conn = get_db()
            cur = conn.execute("DELETE FROM beds WHERE id=?", (bed_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"bed {bed_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": bed_id})
            return

        # マルチ資材DB（mulch_materials）の削除（関連する在庫も削除）。
        if self.path.startswith("/api/mulch_materials/"):
            mat_id = int(self.path.split("/")[-1])
            conn = get_db()
            try:
                with conn:
                    conn.execute("DELETE FROM mulch_inventory WHERE material_id=?", (mat_id,))
                    cur = conn.execute("DELETE FROM mulch_materials WHERE id=?", (mat_id,))
            except sqlite3.Error:
                conn.close()
                self._send_json(500, {"error": "failed to delete mulch_material"})
                return
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"mulch_material {mat_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": mat_id})
            return

        # マルチ在庫（mulch_inventory）の削除。
        if self.path.startswith("/api/mulch_inventory/"):
            inv_id = int(self.path.split("/")[-1])
            conn = get_db()
            cur = conn.execute("DELETE FROM mulch_inventory WHERE id=?", (inv_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"mulch_inventory {inv_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": inv_id})
            return

        # 検鏡場所マスターの削除。
        if self.path.startswith("/api/inspection_locations/"):
            loc_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute(
                "DELETE FROM inspection_locations WHERE id=?", (int(loc_id),))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"inspection_location {loc_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": loc_id})
            return

        # 従業者マスター（workers）削除。
        if self.path.startswith("/api/workers/"):
            w_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM workers WHERE id=?", (int(w_id),))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"worker {w_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": w_id})
            return

        # コホート（育苗バッチ）マスター削除。
        if self.path.startswith("/api/cohorts/"):
            c_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM cohorts WHERE id=?", (int(c_id),))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"cohort {c_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": c_id})
            return

        # 定植予定（transplant_plan）削除。
        if self.path.startswith("/api/transplant_plan/"):
            tp_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM transplant_plan WHERE id=?", (int(tp_id),))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"transplant_plan {tp_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": tp_id})
            return

        # 熱消毒予定（heat_schedule）削除。
        if self.path.startswith("/api/heat_schedule/"):
            hs_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM heat_schedule WHERE id=?", (int(hs_id),))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"heat_schedule {hs_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": hs_id})
            return

        if self.path.startswith("/api/spray_history?date="):
            date = self.path.split("?date=")[1]
            conn = get_db()
            cur = conn.execute("DELETE FROM spray_history WHERE date=?", (date,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"record {date} not found"})
                return
            self._send_json(200, {"status": "deleted", "date": date})
            return

        if self.path.startswith("/api/spray_schedule/"):
            sched_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM spray_schedule WHERE id=?", (sched_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"spray_schedule {sched_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": sched_id})
            return

        # 定植暦の削除。
        if self.path.startswith("/api/transplant_schedule/"):
            t_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM transplant_schedule WHERE id=?", (t_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"transplant_schedule {t_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": t_id})
            return

        if self.path.startswith("/api/inventory/"):
            inv_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM inventory WHERE id=?", (inv_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": inv_id})
            return

        self._send_json(404, {"error": "not found"})
        return

    # ── POST ─────────────────────────────────────────────────────

    def do_POST(self):
        if self.path.startswith("/api/rbp/"):
            self._handle_rbp_post()
            return

        # マルチ敷設（task_id='mulch'）のライブ実行 — 認知→評価→決定→投射→作動
        if self.path == "/api/mulch/prescribe":
            self._handle_mulch_prescribe()
            return

        # 運航管理センター（operations）— 業務定義（schedule_def）の登録・削除
        if self.path.startswith("/api/operations/"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            if self.path == "/api/operations/defs":
                self._handle_operations_defs_post(body)
            elif self.path == "/api/operations/defs/delete":
                self._handle_operations_defs_delete(body)
            elif self.path == "/api/operations/ai-interpret":
                self._handle_operations_ai_interpret(body)
            else:
                self._send_json(404, {"error": "unknown operations endpoint"})
            return

        if self.path == "/api/pesticides":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            existing = conn.execute("SELECT id FROM pesticides WHERE id=?", (body.get("id"),)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f'pesticide {body.get("id")} already exists'})
                return

            required = ["id", "name", "activeIngredient", "category", "targetVector", "targetNames"]
            missing = [f for f in required if f not in body]
            if missing:
                conn.close()
                self._send_json(400, {"error": f"missing fields: {missing}"})
                return
            if not isinstance(body["targetVector"], list) or len(body["targetVector"]) != VECTOR_DIM:
                conn.close()
                self._send_json(400, {"error": f"targetVector must be {VECTOR_DIM}-length array"})
            if not isinstance(body["targetNames"], list):
                conn.close()
                self._send_json(400, {"error": "targetNames must be an array"})

            conn.execute(
                """INSERT INTO pesticides
                   (id, name, activeIngredient, category, targetVector, targetNames,
                    phiDays, mixingRestriction, mixingBanTargets, maxApplications,
                    toxicityClass, system, systemCode, dilutionRate,
                    brand, formulation, packaging, contentForm, applicationRate,
                    capacity, packSize, packUnit, price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    body["id"],
                    body["name"],
                    body.get("activeIngredient"),
                    body.get("category"),
                    json.dumps(body["targetVector"]),
                    json.dumps(body["targetNames"]),
                    body.get("phiDays"),
                    body.get("mixingRestriction"),
                    json.dumps(body.get("mixingBanTargets", [])),
                    body.get("maxApplications"),
                    body.get("toxicityClass"),
                    body.get("system"),
                    body.get("systemCode"),
                    body.get("dilutionRate"),
                    body.get("brand"),
                    body.get("formulation"),
                    body.get("packaging"),
                    body.get("contentForm"),
                    body.get("applicationRate"),
                    body.get("capacity"),
                    body.get("packSize"),
                    body.get("packUnit"),
                    body.get("price"),
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": body["id"]})
            return

        if self.path == "/api/diseases":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            existing = conn.execute("SELECT id FROM diseases WHERE id=?", (int(body.get("id")),)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f'disease {body.get("id")} already exists'})
                return

            required = ["id", "name", "type"]
            missing = [f for f in required if f not in body]
            if missing:
                conn.close()
                self._send_json(400, {"error": f"missing fields: {missing}"})
                return
            if body["type"] not in ("disease", "pest"):
                conn.close()
                self._send_json(400, {"error": 'type must be "disease" or "pest"'})
                return

            conn.execute(
                "INSERT INTO diseases (id, name, type, icon) VALUES (?, ?, ?, ?)",
                (int(body["id"]), body["name"], body["type"], body.get("icon")),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": body["id"]})
            return

        if self.path == "/api/fields":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            name = (body.get("name") or "").strip()
            ftype = body.get("type")
            area_value = body.get("area_value")
            area_unit = body.get("area_unit")
            spray_rate = body.get("spray_rate")
            spray_volume = body.get("spray_volume")

            if not name:
                self._send_json(400, {"error": "missing field: name"})
                return
            if ftype not in ("nursery", "main"):
                self._send_json(400, {"error": "type must be 'nursery' or 'main'"})
                return
            if area_unit is not None and area_unit not in ("m2", "tan", "tsubo"):
                self._send_json(400, {"error": "area_unit must be one of m2/tan/tsubo or null"})
                return
            if area_value is not None:
                try:
                    area_value = float(area_value)
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "area_value must be a number or null"})
                    return
            if spray_rate is not None:
                try:
                    spray_rate = float(spray_rate)
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "spray_rate must be a number or null"})
                    return
            if spray_volume is not None:
                try:
                    spray_volume = float(spray_volume)
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "spray_volume must be a number or null"})
                    return

            conn = get_db()
            existing = conn.execute("SELECT id FROM fields WHERE name=?", (name,)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f"field '{name}' already exists"})
                return

            now = datetime.datetime.utcnow().isoformat()
            cur = conn.execute(
                "INSERT INTO fields (name, type, area_value, area_unit, spray_rate, spray_volume, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, ftype, area_value, area_unit, spray_rate, spray_volume, now, now),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # ベッド台帳（beds）の登録（圃場の関連テーブル）。
        if self.path == "/api/beds":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            field_id = body.get("field_id")
            name = (body.get("name") or "").strip()
            if not field_id:
                self._send_json(400, {"error": "missing field: field_id"})
                return
            if not name:
                self._send_json(400, {"error": "missing field: name"})
                return
            conn = get_db()
            if not conn.execute("SELECT 1 FROM fields WHERE id=?", (int(field_id),)).fetchone():
                conn.close()
                self._send_json(400, {"error": f"field {field_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            cur = conn.execute(
                "INSERT INTO beds (field_id, name, seq, length_m, width_m, mulch_type, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(field_id), name, body.get("seq"),
                    body.get("length_m"), body.get("width_m"),
                    body.get("mulch_type"), body.get("notes"), now, now,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # マルチ資材DB（mulch_materials）の登録。
        if self.path == "/api/mulch_materials":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            name = (body.get("name") or "").strip()
            if not name:
                self._send_json(400, {"error": "missing field: name"})
                return
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM mulch_materials WHERE name=?", (name,)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f"mulch_material '{name}' already exists"})
                return
            now = datetime.datetime.utcnow().isoformat()
            cur = conn.execute(
                "INSERT INTO mulch_materials (name, type, width_cm, hole_interval_cm, length_cm, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name, body.get("type"), body.get("width_cm"),
                    body.get("hole_interval_cm"), body.get("length_cm"),
                    body.get("notes"), now, now,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # マルチ在庫（mulch_inventory）の登録。
        if self.path == "/api/mulch_inventory":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            quantity = body.get("quantity")
            if quantity is None:
                self._send_json(400, {"error": "missing field: quantity"})
                return
            try:
                quantity = float(quantity)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "quantity must be a number"})
                return
            conn = get_db()
            material_id = body.get("material_id")
            if material_id and not conn.execute(
                    "SELECT 1 FROM mulch_materials WHERE id=?", (int(material_id),)).fetchone():
                conn.close()
                self._send_json(400, {"error": f"mulch_material {material_id} not found"})
                return
            field_id = body.get("field_id")
            if field_id and not conn.execute(
                    "SELECT 1 FROM fields WHERE id=?", (int(field_id),)).fetchone():
                conn.close()
                self._send_json(400, {"error": f"field {field_id} not found"})
                return
            now = datetime.datetime.utcnow().isoformat()
            cur = conn.execute(
                "INSERT INTO mulch_inventory (material_id, field_id, quantity, unit, lot_number, expiry_date, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(material_id) if material_id else None,
                    int(field_id) if field_id else None,
                    quantity, body.get("unit") or "m2",
                    body.get("lot_number"), body.get("expiry_date"),
                    body.get("notes"), now, now,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # 検鏡場所マスターの登録。
        if self.path == "/api/inspection_locations":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            name = (body.get("name") or "").strip()
            if not name:
                self._send_json(400, {"error": "missing field: name"})
                return
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM inspection_locations WHERE name=?", (name,)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f"location '{name}' already exists"})
                return
            now = datetime.datetime.utcnow().isoformat()
            cur = conn.execute(
                "INSERT INTO inspection_locations (name, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (name, now, now),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # 従業者マスター（workers）登録。
        if self.path == "/api/workers":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            name = (body.get("name") or "").strip()
            if not name:
                self._send_json(400, {"error": "missing field: name"})
                return
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM workers WHERE name=?", (name,)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f"worker '{name}' already exists"})
                return
            now = datetime.datetime.utcnow().isoformat()
            cur = conn.execute(
                "INSERT INTO workers (name, role, phone, org, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, body.get("role"), body.get("phone"), body.get("org"), now, now),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # コホート（イチゴ栽培サイクル）マスター登録。
        # 命名は「第N期」（通算番号・年またぎに強い）。収穫年・育苗開始年月は属性。
        # name 未指定なら period_no から「第N期」を自動生成。
        if self.path == "/api/cohorts":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            period_no = body.get("period_no")
            try:
                period_no = int(period_no) if period_no not in (None, "") else None
            except (TypeError, ValueError):
                period_no = None
            harvest_year = body.get("harvest_year")
            try:
                harvest_year = int(harvest_year) if harvest_year not in (None, "") else None
            except (TypeError, ValueError):
                harvest_year = None
            name = (body.get("name") or "").strip()
            if not name:
                if period_no is None:
                    self._send_json(400, {"error": "missing field: name or period_no"})
                    return
                name = f"第{period_no}期"
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM cohorts WHERE name=?", (name,)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f"cohort '{name}' already exists"})
                return
            now = datetime.datetime.utcnow().isoformat()
            cur = conn.execute(
                "INSERT INTO cohorts (name, period_no, harvest_year, seedling_start, "
                "field_id, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, period_no, harvest_year, body.get("seedling_start"),
                 body.get("field_id"), body.get("notes"), now, now),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # 定植予定（transplant_plan）登録（コホートの定植・手動オーバーライド）。
        if self.path == "/api/transplant_plan":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            cohort_id = body.get("cohort_id")
            if not cohort_id:
                self._send_json(400, {"error": "missing field: cohort_id"})
                return
            conn = get_db()
            now = datetime.datetime.utcnow().isoformat()
            _aids = _assignee_ids_from_body(body)
            _single = _aids[0] if _aids else None
            # 1 コホート 1 定植（cohort_id で upsert）。
            existing = conn.execute(
                "SELECT id FROM transplant_plan WHERE cohort_id=?", (int(cohort_id),)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE transplant_plan SET schedule_date=?, assignee_id=?, assignee_ids=?, status=?, notes=?, updated_at=? WHERE id=?",
                    (body.get("schedule_date"), _single, json.dumps(_aids),
                     body.get("status", "scheduled"), body.get("notes"), now, existing["id"]))
                conn.commit()
                conn.close()
                self._send_json(200, {"status": "updated", "id": existing["id"]})
                return
            cur = conn.execute(
                "INSERT INTO transplant_plan (cohort_id, schedule_date, assignee_id, assignee_ids, status, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(cohort_id), body.get("schedule_date"), _single, json.dumps(_aids),
                 body.get("status", "scheduled"), body.get("notes"), now, now),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        # 熱消毒予定（heat_schedule）作成（1 コホート 1 熱消毒・cohort_id で upsert）。
        if self.path == "/api/heat_schedule":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            cohort_id = body.get("cohort_id")
            if not cohort_id:
                self._send_json(400, {"error": "missing field: cohort_id"})
                return
            conn = get_db()
            now = datetime.datetime.utcnow().isoformat()
            _aids = _assignee_ids_from_body(body)
            _single = _aids[0] if _aids else None
            # 1 コホート 1 熱消毒（cohort_id で upsert）。
            existing = conn.execute(
                "SELECT id FROM heat_schedule WHERE cohort_id=?", (int(cohort_id),)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE heat_schedule SET start_date=?, deadline_date=?, assignee_id=?, assignee_ids=?, status=?, notes=?, updated_at=? WHERE id=?",
                    (body.get("start_date"), body.get("deadline_date"), _single, json.dumps(_aids),
                     body.get("status", "scheduled"), body.get("notes"), now, existing["id"]))
                conn.commit()
                conn.close()
                self._send_json(200, {"status": "updated", "id": existing["id"]})
                return
            cur = conn.execute(
                "INSERT INTO heat_schedule (cohort_id, start_date, deadline_date, assignee_id, assignee_ids, status, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(cohort_id), body.get("start_date"), body.get("deadline_date"),
                 _single, json.dumps(_aids), body.get("status", "scheduled"), body.get("notes"), now, now),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": cur.lastrowid})
            return

        if self.path == "/api/spray_history":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            date = body.get("date")
            pests = body.get("pests")
            vector = body.get("vector")

            if not date or not pests or vector is None:
                self._send_json(400, {"error": "missing fields: date, pests, vector"})
                return

            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO spray_history (date, pests, vector, field_id) VALUES (?, ?, ?, ?)",
                (date, json.dumps(pests), json.dumps(vector), body.get("field_id")),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "date": date})
            return

        if self.path == "/api/spray_schedule/copy-year":
            self._handle_spray_schedule_copy_year()
            return

        # POST /api/spray_schedule/<id>/generate — 1行分の処方生成を即時実行
        # (UIの「⚡今すぐ」ボタン / cron を待たずRBP実行+DB更新+Slack通知)
        m = re.match(r"^/api/spray_schedule/(\d+)/generate$", self.path)
        if m:
            self._handle_spray_schedule_generate(m.group(1))
            return

        if self.path == "/api/spray_schedule":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            schedule_date = body.get("schedule_date")
            if not schedule_date:
                self._send_json(400, {"error": "missing field: schedule_date"})
                return

            now = datetime.datetime.utcnow().isoformat()
            _aids = _assignee_ids_from_body(body)
            conn = get_db()
            cur = conn.execute(
                """INSERT INTO spray_schedule
                   (schedule_date, actual_date, status, trigger_type, trigger_ref,
                    eval_box_id, rb_out_json, set_ids, pesticide_ids, operator,
                    assignee_ids, weather, notes, field_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule_date,
                    body.get("actual_date"),
                    body.get("status", "scheduled"),
                    body.get("trigger_type", "cycle"),
                    body.get("trigger_ref"),
                    body.get("eval_box_id"),
                    json.dumps(body.get("rb_out_json")) if body.get("rb_out_json") is not None else None,
                    json.dumps(body.get("set_ids", [])),
                    json.dumps(body.get("pesticide_ids", [])),
                    body.get("operator"),
                    json.dumps(_aids),
                    body.get("weather"),
                    body.get("notes"),
                    body.get("field_id"),
                    now,
                    now,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            self._send_json(200, {"status": "created", "id": new_id})
            return

        # 定植暦（花芽分化確認）の登録。
        if self.path == "/api/transplant_schedule":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            schedule_date = body.get("schedule_date")
            if not schedule_date:
                self._send_json(400, {"error": "missing field: schedule_date"})
                return
            now = datetime.datetime.utcnow().isoformat()
            _aids = _assignee_ids_from_body(body)
            _single = _aids[0] if _aids else None
            conn = get_db()
            cur = conn.execute(
                """INSERT INTO transplant_schedule
                   (schedule_date, start_time, end_time, judge_date, judge_result,
                    judge_org, judge_location, status, notes, field_id,
                    cohort_id, seq, is_final, assignee_id, assignee_ids,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule_date,
                    body.get("start_time"),
                    body.get("end_time"),
                    body.get("judge_date"),
                    body.get("judge_result"),
                    body.get("judge_org") or "那賀地方いちご生産組合連合会",
                    body.get("judge_location"),
                    body.get("status", "scheduled"),
                    body.get("notes"),
                    body.get("field_id"),
                    body.get("cohort_id"),
                    body.get("seq"),
                    int(body.get("is_final") or 0),
                    _single,
                    json.dumps(_aids),
                    now,
                    now,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            self._send_json(200, {"status": "created", "id": new_id})
            return

        if self.path.startswith("/api/inventory"):
            self._handle_inventory_post()
            return

        if self.path not in ("/api/eval-boxes", "/api/prescribe", "/api/chat/message", "/api/chat-webhook", "/api/designer/save", "/api/designer/list", "/api/designer/load", "/api/designer/delete", "/api/tokens/set", "/api/tokens/reset"):
            self._send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if self.path == "/api/chat/message":
            self._handle_chat_message(body)
            return

        if self.path == "/api/prescribe":
            self._handle_prescribe(body)
            return

        if self.path == "/api/chat-webhook":
            self._handle_chat_webhook(body)
            return

        if self.path == "/api/designer/save":
            self._handle_designer_save(body)
            return

        if self.path == "/api/designer/list":
            self._handle_designer_list()
            return

        if self.path == "/api/designer/load":
            self._handle_designer_load(body)
            return

        if self.path == "/api/designer/delete":
            self._handle_designer_delete(body)
            return

        if self.path == "/api/tokens/set":
            self._handle_tokens_set(body)
            return

        if self.path == "/api/tokens/reset":
            self._handle_tokens_reset(body)
            return

        box_id = body.get("id")
        name = body.get("name")
        vector = body.get("vector")

        if not isinstance(box_id, str) or not box_id:
            self._send_json(400, {"error": "id must be a non-empty string"})
            return
        if not isinstance(name, str):
            self._send_json(400, {"error": "name must be a string"})
            return
        if (not isinstance(vector, list) or len(vector) != VECTOR_DIM
                or any(v not in (0, 1) for v in vector)):
            self._send_json(400, {"error": f"vector must be a {VECTOR_DIM}-length array of 0/1"})
            return

        conn = get_db()
        existing = conn.execute("SELECT id FROM eval_boxes_custom WHERE id=?", (box_id,)).fetchone()
        if existing:
            conn.close()
            self._send_json(409, {"error": f"id {box_id} already registered", "existing": {"id": existing["id"], "name": existing["name"]}})
            return

        conn.execute(
            "INSERT INTO eval_boxes_custom (id, name, vector) VALUES (?, ?, ?)",
            (box_id, name, json.dumps(vector)),
        )
        conn.commit()
        conn.close()
        self._send_json(200, {"status": "OK", "id": box_id, "name": name})

    def _handle_prescribe(self, body):
        engine = body.get("engine")
        entry_vector = body.get("entryVector")

        if engine not in ("python", "haskell"):
            self._send_json(400, {"error": "engine must be 'python' or 'haskell'"})
            return
        if (not isinstance(entry_vector, list) or len(entry_vector) != VECTOR_DIM
                or any(v not in (0, 1) for v in entry_vector)):
            self._send_json(400, {"error": f"entryVector must be a {VECTOR_DIM}-length array of 0/1"})
            return

        try:
            if engine == "python":
                result = run_python_engine(entry_vector)
            else:
                result = run_haskell_engine(entry_vector)
        except Exception as e:
            self._send_json(500, {"error": f"{engine} engine error: {e}"})
            return

        status = 500 if isinstance(result, dict) and result.get("error") else 200
        self._send_json(status, result)

    # ── Chat AI ──────────────────────────────────────────────────

    def _serve_chat_page(self):
        """Serve the chat UI page."""
        chat_path = os.path.join(APP_ROOT, "chat.html")
        try:
            with open(chat_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "chat.html not found"})

    def _serve_knowledge_page(self):
        """Serve the 境界言語知識メンテナンス page（/knowledge）— index.html から分離。"""
        kb_path = os.path.join(APP_ROOT, "knowledge.html")
        try:
            with open(kb_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "knowledge.html not found"})

    def _serve_operations_page(self):
        """Serve the 業務運航管理センター page（/operations）— 管制塔 UI。

        ①運行図表（線路図）②異常解釈（AI解読層）③アラートフィード。
        ペトリネットライブは独立ページ /stn（STN・ライブ）へ移し削除済み。
        真界（TrueNet）の UI レイヤー。OS コア（humos_os.plan）と STN の上に載る。
        """
        ops_path = os.path.join(APP_ROOT, "operations.html")
        try:
            with open(ops_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "operations.html not found"})

    def _serve_humos_page(self):
        """Serve the HUMOS OS コンソール page（/humos）— 真界管理画面。

        本質4層（①本質・相互開き ②核心・トラスト形成 ③機構 ④OSである所以）。
        ドメイン非依存: 各ドメインサーバー（STB/DC/将来）が同一 humos.html を配信し、
        そのサーバーの humos_os DB を読む。UI コードはドメインに依存しない。
        設計: ts/真界_HUMOS管理画面設計.md
        """
        humos_path = os.path.join(APP_ROOT, "humos.html")
        try:
            with open(humos_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "humos.html not found"})

    def _serve_stn_page(self):
        """Serve the STN・ライブ page（/stn）— ペトリネット（因果）× ドメイン × 膜（接地）。

        運航管理センター（/operations）のパネル②「STN・ライブ」を独立ページ化したもの。
        ペトリネット（因果）× ドメイン × 膜（接地）のライブビュー
        （時系列再生・ドリルダウン・コホート切替・5段ナレッジ）をフルビューポートで表示。
        自己完結型（CSS・JS インライン・共有モジュールなし）。
        """
        stn_path = os.path.join(APP_ROOT, "stn.html")
        try:
            with open(stn_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "stn.html not found"})

    def _serve_designer_page(self):
        """Serve the LangGraph Designer page."""
        designer_path = os.path.join(APP_ROOT, "langgraph_designer.html")
        try:
            with open(designer_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "langgraph_designer.html not found"})

    def _serve_viz_page(self):
        """Serve the 境界言語図表 page（/viz）— rbp_viz が生DBから動的生成。

        境界言語（共通上位形式 14 テーブル）を図表（2次元境界図・決定表・決定木・
        フロー・要求軸・パイプ構造・代替判断・暦）に写像する。DC の /viz と同型。

        **生DB（rbp_* 知識DB）から動的生成**するため、🧩知識メンテUIでの編集を
        そのまま反映する（静的ファイルより常に新しい）。STB（10次元・完全一致型）
        では 2次元境界図・URGENCY 決定木・要求軸期限図は「非適用」注記で表示され、
        決定表・フロー・パイプ構造・代替判断・暦が描画される（rbp_viz はドメイン無依）。
        """
        conn = get_db()
        try:
            # domain_id を解決（既定 STB-PEST、無ければ rbp_domain の先頭）。
            row = conn.execute("SELECT domain_id FROM rbp_domain WHERE domain_id='STB-PEST'").fetchone()
            if not row:
                row = conn.execute("SELECT domain_id FROM rbp_domain LIMIT 1").fetchone()
            if not row:
                self._send_json(404, {"error": "rbp_domain が空（境界言語が未シード）"})
                return
            domain_id = row["domain_id"]
            try:
                model = rbp_viz.load_domain(conn, domain_id)
                html = rbp_viz.render_html(model, back_href="/")
            except rbp_viz.ModelError as e:
                self._send_json(400, {"error": f"図表モデルエラー: {e}"})
                return
        finally:
            conn.close()
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _handle_chat_message(self, body):
        """Handle chat message → Claude API."""
        message = body.get("message", "").strip()
        if not message:
            self._send_json(400, {"error": "message is required"})
            return

        try:
            from agentic_chat import run as agentic_run
            # thread_id は残互換（状態遷移ネット実行では会話履歴に使われない）
            thread_id = self.client_address[0] if self.client_address else "default"
            response = agentic_run(
                message,
                thread_id=thread_id,
            )
            self._send_json(200, {"response": response})
        except ImportError as e:
            self._send_json(503, {
                "response": f"⛔ agentic_chat モジュールが読み込めません。\n\n詳細: {str(e)}"
            })
        except Exception as e:
            self._send_json(500, {"error": f"チャットエラー: {str(e)[:200]}"})

    def _handle_chat_webhook(self, body):
        """Handle Slack webhook message send request."""
        text = body.get("text", "").strip()
        title = body.get("title", "").strip()
        sections = body.get("sections", [])

        if not text and not title:
            self._send_json(400, {"error": "text or title is required"})
            return

        if not text:
            text = title

        try:
            import sos
            if sections:
                result = sos.slack.send_card(title or text, sections)
            else:
                result = sos.slack.send_message(text)

            if result.get("success"):
                self._send_json(200, {"status": "sent"})
            else:
                self._send_json(500, {"error": result.get("error", "不明なエラー")})
        except ImportError:
            self._send_json(503, {"error": "sos モジュールが見つかりません"})
        except Exception as e:
            self._send_json(500, {"error": f"送信中にエラーが発生しました: {str(e)[:200]}"})

    # ─── LangGraph Designer API ───────────────────────────────────

    def _handle_designer_save(self, body):
        """Save a graph to disk."""
        name = body.get("name", "").strip()
        data = body.get("data")
        if not name or not data:
            self._send_json(400, {"error": "name and data are required"})
            return
        try:
            with _graph_lock:
                filename = _save_graph(name, data)
            self._send_json(200, {"ok": True, "filename": filename})
        except Exception as e:
            self._send_json(500, {"error": f"保存中にエラー: {str(e)[:200]}"})

    def _handle_designer_list(self):
        """List all saved graphs."""
        try:
            with _graph_lock:
                graphs = _list_graphs()
            self._send_json(200, {"graphs": graphs})
        except Exception as e:
            self._send_json(500, {"error": f"一覧取得中にエラー: {str(e)[:200]}"})

    def _handle_designer_load(self, body):
        """Load a specific graph."""
        filename = body.get("filename", "").strip()
        if not filename:
            self._send_json(400, {"error": "filename is required"})
            return
        try:
            with _graph_lock:
                data = _load_graph(filename)
            if data is None:
                self._send_json(404, {"error": "graph not found"})
                return
            self._send_json(200, {"data": data})
        except Exception as e:
            self._send_json(500, {"error": f"読取中にエラー: {str(e)[:200]}"})

    def _handle_designer_delete(self, body):
        """Delete a saved graph."""
        filename = body.get("filename", "").strip()
        if not filename:
            self._send_json(400, {"error": "filename is required"})
            return
        try:
            with _graph_lock:
                ok = _delete_graph(filename)
            if not ok:
                self._send_json(404, {"error": "graph not found"})
                return
            self._send_json(200, {"ok": True})
        except Exception as e:
            self._send_json(500, {"error": f"削除中にエラー: {str(e)[:200]}"})

    # ─── 状態遷移ネット（STN）視覚化 API ─────────────────────

    def _handle_net_get(self):
        """GET /api/net — 状態遷移ネット構造（プレース/トランジション/弧/複合/作動）。

        ?cohort_id=N → **STB ドメインの STN**（コホートネット＋固定 STN の統合）。
          コホート（検鏡鎖＋定植 AND-JOIN）の階層に、防除・在庫確認（固定 STN =
          jobnet.NET）を 4 番目のマクロとして統合した 1 つのネットを返す
          （ts/STBドメインSTN統合設計.md: 「固定 STN」と「第5期」は 1 つ）。
        未指定 → 固定 STN（正=jobnet.py の NET）のみ（後方互換: operations.html /
          humos.html / langgraph_designer.html がこのデフォルトを消費）。
        現在のプレース値と発火可能状態（enabled_when による真の Petri 網発火
        ルールの宣言）も同梱する。
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        cid = qs.get("cohort_id", [None])[0]
        if cid:
            try:
                conn = get_db()
                net = self._cohort_net(conn, int(cid))
                conn.close()
                if net:
                    self._send_json(200, net)
                    return
            except (ValueError, TypeError):
                pass
        data = jobnet.net_to_json()
        # 前段プレース（state.py ストア）の現在値＋発火可能判定
        place_values = jobnet.net_state()  # state.py ストアのみ（後段は実行時にのみ存在）
        data["current_places"] = {
            p["token_key"]: place_values.get(p["token_key"]) for p in jobnet.NET["places"].values()
        }
        data["firing_enabled"] = jobnet.net_firing(place_values)
        self._send_json(200, data)

    def _mulch_req_instance(self, conn) -> dict | None:
        """マルチ敷設要求（オブジェクト接地・§4.1）の要求インスタンスを導出する。

        要求の発生源は**事業主体（三果倉）**（膜弧の起点・§5.1）。圃場（本圃）は
        要求トークンの**文脈**（field_id/field_name）に格降格する。要求は直接
        トランジションに接続せず**必ずプレースに接続**される（§4.1）。スケジュール
        （schedule_def）による要求イベントが発生すると、**熱消毒（T_heat）の直前
        プレース**（P_heat_ready=熱消毒準備・カラー付き）に、**主体＋文脈を付与した**
        要求トークン（mulch_req_token）を発生させる（2026-09-16:「熱消毒要求は熱消毒
        トランジションの一つ前のプレースに接続」）。P_heat_ready は透明マルチ敷設
        （T_mulch）と潅水調整（T_irrigation）の post であり、熱消毒要求が揃うと
        両準備が有効化され、透明マルチ敷設＋潅水調整→熱消毒のチェーンが起動する。

        オブジェクト属性（本圃）= beds（field_id 紐づけ）の延べ長さ・マルチ種別
        （§7.1: 本圃 → ベッド延べ長さ → マルチ敷設）。

        要求の「発生」判定: schedule_def（name=マルチ敷設・target_key=subject）の
        期待時刻 E が来ている（E ≤ now）か。来ている＝要求イベント発生済み＝
        P_heat_ready（熱消毒準備）に要求トークンを置く。未達期（E > now）は待機
        （トークンなし）。

        膜弧の起点は事業主体（三果倉）: 圃場（本圃）は要求トークンの文脈
        （context.field_id）に格降格する（ts/真界_事業主体設計.md §5.1・§5.2）。

        Returns:
            {def_id, name, group_name, target_value, field_id, cadence,
             expected_at, status, beds:[{name,length_m,mulch_type}], total_length_m}
            該当する schedule_def が無ければ None。
        """
        from datetime import datetime
        now = datetime.now()
        row = conn.execute(
            "SELECT def_id, name, target_key, target_value, group_name, "
            "cadence, deadline_spec, recurrence, expected_state "
            "FROM schedule_def WHERE name='マルチ敷設' AND target_key='subject' "
            "ORDER BY def_id LIMIT 1").fetchone()
        if not row:
            return None
        # 役割（位置的角色）の解決: 要求の発生源は役割（事業主）で、保持者を紐付けで解決。
        _rc = _role_context(conn)
        # 期待時刻 E を解決（plan._resolve_expected_time と同一ロジック）。
        # deadline_spec / recurrence は DB では JSON 文字列 → _row_to_dict でパース。
        from humos_os import plan as _plan
        d = _plan._row_to_dict(dict(row))
        E = _plan._resolve_expected_time(
            {"cadence": d["cadence"], "deadline_spec": d["deadline_spec"],
             "recurrence": d["recurrence"]}, now)
        due = E is not None and now >= E
        # オブジェクト属性: target_value（本圃）→ fields → beds（延べ長さ・幅・マルチ種別）。
        field = conn.execute(
            "SELECT id, name FROM fields WHERE name=?", (row["target_value"],)).fetchone()
        beds = []
        total_len = 0.0
        if field:
            beds = [
                {"name": b["name"], "length_m": b["length_m"], "width_m": b["width_m"],
                 "mulch_type": b["mulch_type"]}
                for b in conn.execute(
                    "SELECT name, length_m, width_m, mulch_type FROM beds WHERE field_id=? ORDER BY seq",
                    (field["id"],)).fetchall()
            ]
            total_len = sum(b["length_m"] or 0.0 for b in beds)
        # マルチ選定要求トークンの payload（認知が読む・オブジェクト接地 §4.1）。
        # 本圃 / 定植 / ベッド延長 / 幅。mulch.build_token と同型の構造化 payload。
        # 幅（width_cm）= 延べ長さ最大の type（primary）の資材幅（本圃=白黒→110cm）。
        token_payload = None
        if field:
            primary_type, best_len = None, -1.0
            for b in beds:
                if not b.get("mulch_type"):
                    continue
                tlen = sum(x["length_m"] or 0.0 for x in beds
                           if x.get("mulch_type") == b["mulch_type"])
                if tlen > best_len:
                    best_len, primary_type = tlen, b["mulch_type"]
            width_cm = None
            if primary_type:
                wrow = conn.execute(
                    "SELECT width_cm FROM mulch_materials WHERE type=? ORDER BY id LIMIT 1",
                    (primary_type,)).fetchone()
                width_cm = wrow["width_cm"] if wrow else None
            # 要求トークンの形（ts/真界_事業主体設計.md §5.2）: 主体＋文脈。
            # object=主体（三果倉・膜弧の起点）、圃場（本圃）は文脈（field_id/
            # field_name）に格降格。context=要求の種（定植）。
            token_payload = {
                "object": _SUBJECT_NAME,
                "subject_id": _SUBJECT_ID,
                # 役割（位置的角色）: 要求の発生源は役割（事業主）で、保持者を紐付けで解決。
                "role": _rc["role"],
                "role_id": _rc["role_id"],
                "role_holder": _rc["role_holder"],
                "field_id": field["id"],
                "field_name": field["name"],
                "context": "定植",
                "beds": beds,
                "total_length_m": total_len,
                "width_cm": width_cm,
            }
        return {
            "def_id": row["def_id"],
            "name": row["name"],
            "group_name": row["group_name"] or row["target_value"],
            "target_value": row["target_value"],
            "field_id": field["id"] if field else None,
            # 膜弧の起点=事業主体（三果倉）。圃場（本圃）は文脈。
            "subject_id": _SUBJECT_ID,
            "subject_name": _SUBJECT_NAME,
            "role": _rc["role"],
            "role_holder": _rc["role_holder"],
            "cadence": row["cadence"],
            "expected_at": E.strftime("%Y-%m-%d %H:%M:%S") if E else None,
            "due": due,
            "beds": beds,
            "total_length_m": total_len,
            "token": token_payload,
            # 接地の点（要求トークンを置くプレース）。P_heat_ready=熱消毒準備（カラー付き）:
            # 熱消毒（T_heat）の**直前プレース**に要求トークンが接続される（ユーザー指定
            # 2026-09-16）。P_heat_ready は T_mulch/T_irrigation の post に現れるため構造
            # ルールでは膜として検出できず、フロントはこれを権威的膜プレースとして採用。
            "place": "P_heat_ready",
        }

    def _transplant_req_instance(self, conn) -> dict | None:
        """定植要求（オブジェクト接地・§4.1）の要求インスタンスを導出する。

        マルチ敷設要求（_mulch_req_instance）と同型のオブジェクト接地:
        事業主体（三果倉）のスケジュール（schedule_def・name=定植・
        target_key=subject）発火で、**定植前 P_ready（カラー付き）** に、主体＋文脈
        を付与した要求トークン（transplant_req_token）を置く（ユーザー指定
        2026-09-07: 「定植前のプレースに本圃の定植スケジュールイベントの要求を接続」）。
        膜弧の起点は事業主体: 圃場（本圃）は要求トークンの文脈に格降格
        （ts/真界_事業主体設計.md §5.1・§5.2）。

        マルチ敷設（expected_state=P_ready → 接地先 P_heat_ready）と同様、DB の
        expected_state（=P_transplant=定植完了・T_transplant の post）と接地先
        （=P_ready=定植前・T_transplant の pre）は別。要求は**定植前のゲート**に
        接続される: 定植を始める前に本圃の定植スケジュールが要求を置く。

        Returns:
            {def_id, name, group_name, target_value, field_id, cadence,
             expected_at, due, beds, total_length_m, token, place}
            該当する schedule_def が無ければ None。
        """
        from datetime import datetime
        now = datetime.now()
        row = conn.execute(
            "SELECT def_id, name, target_key, target_value, group_name, "
            "cadence, deadline_spec, recurrence, expected_state "
            "FROM schedule_def WHERE name='定植' AND target_key='subject' "
            "ORDER BY def_id LIMIT 1").fetchone()
        if not row:
            return None
        # 役割（位置的角色）の解決: 要求の発生源は役割（事業主）で、保持者を紐付けで解決。
        _rc = _role_context(conn)
        from humos_os import plan as _plan
        d = _plan._row_to_dict(dict(row))
        E = _plan._resolve_expected_time(
            {"cadence": d["cadence"], "deadline_spec": d["deadline_spec"],
             "recurrence": d["recurrence"]}, now)
        due = E is not None and now >= E
        # オブジェクト属性: target_value（本圃）→ fields → beds（延べ長さ・幅・マルチ種別）。
        field = conn.execute(
            "SELECT id, name FROM fields WHERE name=?", (row["target_value"],)).fetchone()
        beds = []
        total_len = 0.0
        if field:
            beds = [
                {"name": b["name"], "length_m": b["length_m"], "width_m": b["width_m"],
                 "mulch_type": b["mulch_type"]}
                for b in conn.execute(
                    "SELECT name, length_m, width_m, mulch_type FROM beds WHERE field_id=? ORDER BY seq",
                    (field["id"],)).fetchall()
            ]
            total_len = sum(b["length_m"] or 0.0 for b in beds)
        # 定植要求トークンの payload（認知が読む・オブジェクト接地 §4.1）。
        # 本圃 / 定植 / ベッド延長 / 幅。マルチ敷設要求と同型の構造化 payload。
        token_payload = None
        if field:
            primary_type, best_len = None, -1.0
            for b in beds:
                if not b.get("mulch_type"):
                    continue
                tlen = sum(x["length_m"] or 0.0 for x in beds
                           if x.get("mulch_type") == b["mulch_type"])
                if tlen > best_len:
                    best_len, primary_type = tlen, b["mulch_type"]
            width_cm = None
            if primary_type:
                wrow = conn.execute(
                    "SELECT width_cm FROM mulch_materials WHERE type=? ORDER BY id LIMIT 1",
                    (primary_type,)).fetchone()
                width_cm = wrow["width_cm"] if wrow else None
            # 要求トークンの形（ts/真界_事業主体設計.md §5.2）: 主体＋文脈。
            # object=主体（三果倉・膜弧の起点）、圃場（本圃）は文脈（field_id/
            # field_name）に格降格。context=要求の種（定植）。
            token_payload = {
                "object": _SUBJECT_NAME,
                "subject_id": _SUBJECT_ID,
                # 役割（位置的角色）: 要求の発生源は役割（事業主）で、保持者を紐付けで解決。
                "role": _rc["role"],
                "role_id": _rc["role_id"],
                "role_holder": _rc["role_holder"],
                "field_id": field["id"],
                "field_name": field["name"],
                "context": "定植",
                "beds": beds,
                "total_length_m": total_len,
                "width_cm": width_cm,
            }
        return {
            "def_id": row["def_id"],
            "name": row["name"],
            "group_name": row["group_name"] or row["target_value"],
            "target_value": row["target_value"],
            "field_id": field["id"] if field else None,
            # 膜弧の起点=事業主体（三果倉）。圃場（本圃）は文脈。
            "subject_id": _SUBJECT_ID,
            "subject_name": _SUBJECT_NAME,
            "role": _rc["role"],
            "role_holder": _rc["role_holder"],
            "cadence": row["cadence"],
            "expected_at": E.strftime("%Y-%m-%d %H:%M:%S") if E else None,
            "due": due,
            "beds": beds,
            "total_length_m": total_len,
            "token": token_payload,
            # 接地の点（要求トークンを置くプレース）。P_ready=定植前（カラー付き）:
            # 定植時期（timing_token）等と**同じプレース**に要求トークンが接続される
            # （ユーザー指定 2026-09-07）。P_ready は T_transplant の pre に現れるため
            # 構造ルールでは膜として検出できず、フロントはこれを権威的膜プレースとして採用。
            "place": "P_ready",
        }

    def _rx_req_instance(self, conn) -> dict | None:
        """防除要求（オブジェクト接地・§4.1・IF 入方向）の要求インスタンスを導出する。

        マルチ敷設要求（_mulch_req_instance）・定植要求（_transplant_req_instance）と
        同型のオブジェクト接地: 事業主体（三果倉）の防除暦
        （spray_schedule・field_id=1）発火で、**防除サブネットの入口ゲート
        P_rx_req（防除要求）** に、主体＋文脈を付与した要求トークン
        （rx_req_token）を置く。T_rx_select（薬剤選定）の pre に接続し、事業主体 →
        P_rx_req の膜弧（IF 入方向・§8.8）で要求が発射される。圃場（育苗圃）は
        要求トークンの文脈（field_id/field_name）に格降格（ts/真界_事業主体設計.md §5.1）。

        マルチ/定植が schedule_def（単一行・target_key=subject）で駆動されるのに対し、
        防除は spray_schedule（複数行・育苗圃 65 件）で駆動される。膜弧は「事業主体
        → P_rx_req」の**1 本の要求**として表現し、代表は**次回散布予定**（未実施で最も
        近い schedule_date）を採用する（65 本の弧はノイズ）。

        Returns:
            {def_id, name, group_name, target_value, field_id, cadence,
             expected_at, due, total_count, token, place}
            育苗圃 に紐づく spray_schedule が無ければ None。
        """
        from datetime import datetime
        now = datetime.now()
        # ドメインオブジェクト = 育苗圃（fields.type='nursery'）。育苗圃は育苗に
        # おける防除の対象（本圃でも行われるが、膜弧の代表は育苗圃）。
        field = conn.execute(
            "SELECT id, name FROM fields WHERE type='nursery' ORDER BY id LIMIT 1").fetchone()
        if not field:
            return None
        rows = conn.execute(
            "SELECT id, schedule_date, actual_date, status, notes "
            "FROM spray_schedule WHERE field_id=? AND schedule_date IS NOT NULL "
            "ORDER BY schedule_date", (field["id"],)).fetchall()
        if not rows:
            return None
        done_statuses = {"done", "completed", "finished", "actual"}
        # 無効日付（例: 2026-02-29=非閏年の2/29）は _spray_instances と同様にスキップ。
        # 代表候補 = 日付が有効な行のみ。
        valid = []
        for r in rows:
            try:
                datetime.strptime(r["schedule_date"], "%Y-%m-%d")
            except ValueError:
                continue
            valid.append(r)
        if not valid:
            return None
        total = len(valid)
        # 代表 = 未実施（実績なし）で最も近い散布予定。全て実施済みなら最終予定日。
        pending = [r for r in valid
                   if not r["actual_date"] and (r["status"] or "") not in done_statuses]
        rep = (pending or [valid[-1]])[0]
        sd = rep["schedule_date"]
        try:
            D = datetime.strptime(sd, "%Y-%m-%d")
        except ValueError:
            D = None
        due = D is not None and now >= D
        # 役割（位置的角色）の解決: 要求の発生源は役割（事業主）で、保持者を紐付けで解決。
        _rc = _role_context(conn)
        # 要求トークンの payload（認知が読む・オブジェクト接地 §4.1）。
        # 主体（三果倉）/ 防除 / 圃場（育苗圃・文脈）/ 次回散布予定 / 暦全体件数。
        # object=主体（膜弧の起点）、圃場は文脈（field_id/field_name）に格降格
        # （ts/真界_事業主体設計.md §5.1・§5.2）。
        token_payload = {
            "object": _SUBJECT_NAME,
            "subject_id": _SUBJECT_ID,
            # 役割（位置的角色）: 要求の発生源は役割（事業主）で、保持者を紐付けで解決。
            "role": _rc["role"],
            "role_id": _rc["role_id"],
            "role_holder": _rc["role_holder"],
            "field_id": field["id"],
            "field_name": field["name"],
            "context": "防除",
            "next_spray_date": sd,
            "next_spray_notes": rep["notes"],
            "total_count": total,
        }
        return {
            "def_id": f"sp-{rep['id']}",
            "name": "防除",
            "group_name": field["name"],
            "target_value": field["name"],
            "field_id": field["id"],
            # 膜弧の起点=事業主体（三果倉）。圃場（育苗圃）は文脈。
            "subject_id": _SUBJECT_ID,
            "subject_name": _SUBJECT_NAME,
            "role": _rc["role"],
            "role_holder": _rc["role_holder"],
            "cadence": "interval",
            "expected_at": f"{sd} 08:00:00",
            "due": due,
            "total_count": total,
            "token": token_payload,
            # 接地の点（要求トークンを置くプレース）。P_rx_req=防除要求（T_rx_select の
            # pre・防除サブネットの入口ゲート）。pre に現れて post に現れないため構造
            # ルール（membranePlaces）でも膜として検出されるが、server が権威的に宣言し
            # フロントは事業主体（膜弧の起点）を代表として対応させる。
            "place": "P_rx_req",
        }

    def _cohort_net(self, conn, cid):
        """コホート（育苗バッチ）ごとのペトリネット（部分定義）を生成する。

        設計: 検鏡サブネット（順送り＋分化ありで後続スキップ＋最終で強制発行）
        ＋ 定植（T_transplant）への AND-JOIN。
        定植は「一つ」のプレプレース P_ready（定植前）を持ち、その中に
        「定植時期 / マルチ敷設 / 薬剤準備 / 手袋揃い」の複数トークン
        （カラー）が収束する（カラー付きペトリネットの AND-JOIN 表現）。

        検鏡サブネット（検鏡1→…→最終）:
          - T_judge{seq}: 判定済みで P_judge{seq} に判定トークン（あり/なし）。
          - T_ari{seq}（分化あり）: P_judge{seq} が充足 → P_ready に「定植時期」
            トークンを発行し、T_skip{seq} 経由で後続検鏡をスキップする。
          - T_force（最終で分化なし）: 最終検鏡 P_judge{last} が充足 → P_ready に
            「定植時期」トークンを強制発行する（分化なくとも定植時期は確定）。
        各準備トランジション（T_multilay / T_prep / T_gloves）は P_ready に
        1 トークンずつ deposit する。P_ready に全必須トークンが揃うと
        T_transplant が発火する。検鏡は外部判定待ち（P_external_wait）。

        マーキング（token_key→値）は DB の実状態から導出する:
          - 検鏡判定済み（judge_date or status=confirmed）→ P_judge{seq}
          - 定植時期（分化あり or 最終で分化なし）→ P_ready の timing_token
          - 定植完了（transplant_plan.status=done）→ P_transplant
        発火可能（firing_enabled）は真の Petri 網発火ルール（pre 全プレースの
        全トークン有無）で判定。返り値は renderPetri 互換（places/transitions/
        edges/entry/firing_enabled + current_places）。コホート無ければ None。
        """
        row = conn.execute("SELECT * FROM cohorts WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        cname = row["name"] or f"コホート{cid}"

        # 検鏡を seq 順に取得（seq 未設定は日付順で補完）。
        insps = conn.execute(
            "SELECT seq, judge_date, judge_result, status, is_final "
            "FROM transplant_schedule WHERE cohort_id=? AND schedule_date IS NOT NULL "
            "ORDER BY COALESCE(seq, 0), schedule_date", (cid,)).fetchall()
        if not insps:
            insps = conn.execute(
                "SELECT seq, judge_date, judge_result, status, is_final "
                "FROM transplant_schedule WHERE cohort_id=? "
                "ORDER BY COALESCE(seq, 0), schedule_date", (cid,)).fetchall()
        # seq 補完（未設定は出現順 1,2,3…）
        seqs = []
        for r in insps:
            seqs.append(r["seq"] if r["seq"] else (seqs[-1] + 1 if seqs else 1))
        plan_row = conn.execute(
            "SELECT * FROM transplant_plan WHERE cohort_id=?", (cid,)).fetchone()

        # ---- 検鏡サブネットの状態導出（順送り＋分化ありで後続スキップ＋最終で強制発行） ----
        # 各検鏡の判定状態を seq 順に算出する:
        #   judged : 判定済み（judge_date or status=confirmed）
        #   ari    : 判定=分化あり（→ 定植時期発行、後続の検鏡をスキップ）
        #   nashi  : 判定=分化なし
        #   final  : 最終検鏡
        #   skipped: 直前の分化ありで後続がスキップされた（未判定のまま）
        def _judged(r):
            return bool(r["judge_date"]) or (r["status"] or "") == "confirmed"
        def _res(r):
            return (r["judge_result"] or "").strip()
        insp_state = []
        prev_ari = False
        for r, seq in zip(insps, seqs):
            judged = _judged(r)
            res = _res(r)
            ari = judged and ("あり" in res)
            nashi = judged and ("なし" in res)
            final = bool(r["is_final"])
            skipped = prev_ari and not judged
            insp_state.append({"seq": seq, "judged": judged, "result": res,
                               "ari": ari, "nashi": nashi, "final": final, "skipped": skipped})
            prev_ari = prev_ari or ari
        # トリガー: 分化あり（初回確定）または 最終で分化なし（強制発行）→ 定植時期。
        # 検鏡の結果は「定植時期が確定した」こと（ありでも、最終でなくとも）。
        timing_determined = any(s["ari"] for s in insp_state) or \
            any(s["nashi"] and s["final"] for s in insp_state)
        t_done = bool(plan_row and plan_row["status"] == "done")

        # ---- マーキング（DB 実状態 → token_key→値） ----
        marking = {}
        for s in insp_state:
            if s["judged"]:
                marking[f"judge_token_{s['seq']}"] = s["result"] or "判定済み"
        if timing_determined:
            marking["timing_token"] = "定植時期"
        if t_done:
            marking["transplant_token"] = "定植完了"

        # ---- マルチ敷設要求トークン（オブジェクト接地・§4.1） ----
        # 事業主体（三果倉）のスケジュール発火で **P_heat_ready（熱消毒準備・カラー付き）**
        # に置かれる要求トークン。本舗属性（ベッド延べ長さ・マルチ種別）を付与した
        # 「マルチ敷設要求」トークン。膜弧の起点=事業主体（圃場・本圃は文脈に格降格・
        # §5.1）。熱消毒（T_heat）の直前プレースに接続（2026-09-16）。
        mulch_req = self._mulch_req_instance(conn)
        if mulch_req and mulch_req["due"]:
            # トークン値 = 本舗属性を付与した要求（誰の・どんな要求か自己完結）。
            # {label, payload}: label=表示文字列 / payload=認知が読む構造化要求
            # （本圃/定植/ベッド延長/幅・オブジェクト接地 §4.1）。
            mulch_types = "・".join(sorted({b["mulch_type"] for b in mulch_req["beds"] if b["mulch_type"]})) or "—"
            # R3: 表示ラベルは主体先頭（要求の発生源=事業主体・役割）。圃場は文脈。
            marking["mulch_req_token"] = {
                "label": (
                    f"{mulch_req['subject_name']}・{mulch_req['role']}：マルチ敷設要求"
                    f"（{mulch_req['group_name']}・延べ{mulch_req['total_length_m']:g}m・{mulch_types}）"
                ),
                "payload": mulch_req.get("token"),
            }

        # ---- 定植要求トークン（オブジェクト接地・§4.1） ----
        # 事業主体（三果倉）の定植スケジュール発火で **P_ready（定植前・カラー付き）**
        # に置かれる要求トークン。マルチ敷設要求（→P_heat_ready）と同型: 本舗属性
        # （ベッド延べ長さ・マルチ種別）を付与した「定植要求」トークンを、定植を始める
        # 前のゲート（定植前）に接続する（2026-09-07）。膜弧の起点=事業主体（圃場は文脈）。
        transplant_req = self._transplant_req_instance(conn)
        if transplant_req and transplant_req["due"]:
            # トークン値 = 本舗属性を付与した要求（誰の・どんな要求か自己完結）。
            # {label, payload}: label=表示文字列 / payload=認知が読む構造化要求。
            t_mulch_types = "・".join(sorted({b["mulch_type"] for b in transplant_req["beds"] if b["mulch_type"]})) or "—"
            # R3: 表示ラベルは主体先頭（要求の発生源=事業主体・役割）。圃場は文脈。
            marking["transplant_req_token"] = {
                "label": (
                    f"{transplant_req['subject_name']}・{transplant_req['role']}：定植要求"
                    f"（{transplant_req['group_name']}・延べ{transplant_req['total_length_m']:g}m・{t_mulch_types}）"
                ),
                "payload": transplant_req.get("token"),
            }

        # ---- 防除要求トークン（オブジェクト接地・§4.1・IF 入方向） ----
        # 事業主体（三果倉）の防除暦（spray_schedule・field_id=1）発火で
        # **P_rx_req（防除要求・T_rx_select の pre・防除入口ゲート）** に置かれる要求
        # トークン。マルチ/定植要求（→P_heat_done / P_ready）と同型で、事業主体 →
        # P_rx_req の膜弧（IF 入方向）で接続する（育苗圃は文脈に格降格・§5.1）。
        # 代表は次回散布予定。
        rx_req = self._rx_req_instance(conn)
        if rx_req and rx_req["due"]:
            # トークン値 = 育苗圃属性を付与した要求（誰の・どんな要求か自己完結）。
            # R3: 表示ラベルは主体先頭（要求の発生源=事業主体・役割）。圃場は文脈。
            marking["rx_req_token"] = {
                "label": (
                    f"{rx_req['subject_name']}・{rx_req['role']}：防除要求"
                    f"（{rx_req['group_name']}・次回 {rx_req['expected_at'][:10]}・暦 {rx_req['total_count']} 件）"
                ),
                "payload": rx_req.get("token"),
            }

        # ---- プレース ----
        places = {}
        for seq in seqs:
            places[f"P_judge{seq}"] = {"name": f"検鏡{seq}判定", "token_key": f"judge_token_{seq}", "store": "cohort"}
        # 定植前 P_ready はカラー付きプレース: 1 プレースに複数トークン（colors）を
        # 保持する。各 colors は {token_key, label}。T_transplant は全 colors が
        # 揃う（AND-JOIN）と発火する。
        # ⑤ transplant_req_token = 事業主体（三果倉）が定植スケジュール発火で置く
        #    「定植要求」トークン（オブジェクト接地 §4.1: 要求は必ずプレースに接続）。
        #    マルチ敷設要求（→P_heat_done）と同型で、定植を始める前のゲート（定植前）に
        #    接続される（2026-09-07）。膜弧の起点=事業主体（圃場・本圃は文脈）。
        places["P_ready"] = {
            "name": "定植前",
            "store": "cohort",
            "colors": [
                {"token_key": "timing_token", "label": "定植時期"},
                {"token_key": "multilay_token", "label": "マルチ敷設"},
                {"token_key": "prep_token", "label": "薬剤準備"},
                {"token_key": "gloves_token", "label": "手袋揃い"},
                {"token_key": "transplant_req_token", "label": "定植要求"},
            ],
        }
        # 熱消毒サブネット（ベッド準備）のプレース。定植の1か月前に着手し、
        # 3週間以上かけて実施する前段フェーズ（同一コホート＝第5期）。
        # 熱消毒準備 P_heat_ready は**カラー付き（複数トークン）プレース**（AND-JOIN ゲート）。
        # ① mulch_token = 透明マルチ敷設（T_mulch）が完了して置く「透明マルチ敷設」トークン。
        # ② irrigation_token = 潅水調整（T_irrigation）が完了して置く「潅水調整」トークン。
        # ③ mulch_req_token = 事業主体（三果倉）がスケジューラ発火で置く「マルチ敷設要求」
        #    トークン（オブジェクト接地 §4.1: 要求は必ずプレースに接続）。要求は独立した
        #    P_mulch_req を作らず、**熱消毒（T_heat）の直前プレース（P_heat_ready）**に
        #    接続される（2026-09-16）。膜弧の起点=事業主体。要求＋両準備が揃うと
        #    T_heat（熱消毒）が発火し、熱消毒完了（P_heat_done）へ進む。
        places["P_heat_ready"] = {
            "name": "熱消毒準備",
            "store": "cohort",
            "colors": [
                {"token_key": "mulch_token", "label": "透明マルチ敷設"},
                {"token_key": "irrigation_token", "label": "潅水調整"},
                {"token_key": "mulch_req_token", "label": "マルチ敷設要求"},
            ],
        }
        # 熱消毒完了 P_heat_done は**カラー付き（複数トークン）プレース**。
        # heat_token = 熱消毒（T_heat）が完了して置く「熱消毒完了」トークン。
        # 熱消毒完了が揃うと T_mulch_select（マルチ選定）が発火し、マルチ3ジョブが開始。
        places["P_heat_done"] = {
            "name": "熱消毒完了",
            "store": "cohort",
            "colors": [
                {"token_key": "heat_token", "label": "熱消毒完了"},
            ],
        }
        # マルチ3ジョブ（選定→在庫確認→敷設）の中間プレース。防除（薬剤選定→在庫確認→
        # 散布）と同型の直列チェーン（§12.7 マルチ敷設の3ジョブ分解）。
        places["P_mulch_select"] = {"name": "マルチ選定", "token_key": "mulch_select_token", "store": "cohort"}
        places["P_mulch_inv"] = {"name": "マルチ在庫確認", "token_key": "mulch_inv_token", "store": "cohort"}
        places["P_transplant"] = {"name": "定植完了", "token_key": "transplant_token", "store": "cohort"}
        # 防除3ジョブ（薬剤選定→在庫確認→散布）の中間プレース。マルチ敷設（選定→在庫→
        # 敷設）と同型の直列チェーン（§12.7）。防除はコホートネットと因果的に独立
        # （病害虫予測で起動）なので、コホートのプレースを参照しない独立サブネット。
        # 防除要求（オブジェクト接地・§4.1・IF 入方向）: 事業主体（三果倉）の防除暦
        # （spray_schedule）発火で、防除サブネットの**入口ゲート**に置かれる要求トークン
        # （rx_req_token）。防除はコホートネットと因果的に独立（病害虫予測で起動）だが、
        # 要求は必ずプレースに接続する（§4.1）。T_rx_select（薬剤選定）の pre として
        # 「防除の一つ前のプレース」を新設し、事業主体 → P_rx_req の膜弧（IF）で接続
        # （育苗圃は文脈に格降格・§5.1）。マルチ敷設要求（P_heat_done）・定植要求
        # （P_ready）と同型の権威的膜。
        places["P_rx_req"] = {"name": "防除要求", "token_key": "rx_req_token", "store": "cohort"}
        places["P_rx_select"] = {"name": "薬剤選定", "token_key": "rx_select_token", "store": "cohort"}
        places["P_rx_inv"] = {"name": "防除在庫確認", "token_key": "rx_inv_token", "store": "cohort"}

        # ---- トランジション（検鏡サブネット: 順送り＋分化ありスキップ＋最終強制発行） ----
        # ペトリネットの構造（P/T/弧）は固定。判定状態（あり/なし）はマーキングと
        # 発火可能（enabled）で表現し、トランジション自体は常時存在させる。
        #   T_judge{seq}: 判定済みで P_judge{seq} に判定トークン（あり/なし）。
        #   T_ari{seq}: 分化あり → 定植時期トークン発行（P_ready）＋ 後続検鏡スキップ。
        #     pre=P_judge{seq} が充足（判定あり）で発火可能。
        #   T_force: 最終検鏡で分化なしでも → 定植時期トークンを強制発行（P_ready）。
        #     pre=最終 P_judge{last} が充足（判定なし）で発火可能。
        #   T_skip{seq}: 後続検鏡 T_judge{seq} をスキップ（制御弧、pre/post 空）。
        # ナレッジ編集導線（/knowledge の rbp_* テーブル）。コホートは写生（ナレッジ未実装）
        # だが、各 T が「どこにナレッジを定義すべきか」を示す。普遍構造の段に対応:
        #   検鏡=認知(顕微鏡画像) / 分化あり・強制発行=決定(定植時期仕様) /
        #   準備・熱消毒・定植=作動(物理界的作動・T の正体)。
        # 各 T の「どこにナレッジを定義すべきか」の rbp_* テーブル（普遍構造の段に対応）。
        _KT = {
            "judge": "rbp_perception_axis",   # 検鏡=認知
            "skip":  "rbp_domain",            # スキップ=制御（ナレッジ不要・作動領域）
            "ari":   "rbp_spec_condition",    # 分化あり=決定（定植時期仕様）
            "force": "rbp_spec_condition",    # 強制発行=決定
            "prep":  "rbp_domain",            # 準備・熱消毒・定植=作動
        }
        # 各 T が属するタスク（業務）単位。/knowledge?task=<task_id> でタスク単位の
        # ナレッジを編集する。コホートは写生（5段未実装=0/5）で逐次埋めていく。
        _TASK = {
            "judge": "inspection", "skip": "inspection", "ari": "inspection",
            "force": "inspection",
            "mulch_select": "mulch", "mulch_inv": "mulch", "multilay": "mulch",
            "prep": "prep", "gloves": "gloves",
            "mulch": "clear-mulch", "irrigation": "irrigation",
            "heat": "heat", "transplant": "transplant",
        }
        transitions = {}
        for seq in seqs:
            transitions[f"T_judge{seq}"] = {
                "name": f"検鏡{seq}", "pre": [], "post": [f"P_judge{seq}"],
                "enabled_when": [], "knowledge_table": _KT["judge"], "task_id": _TASK["judge"],
            }
            if seq < seqs[-1]:
                transitions[f"T_skip{seq}"] = {
                    "name": f"検鏡{seq}スキップ", "pre": [], "post": [],
                    "enabled_when": [], "knowledge_table": _KT["skip"], "task_id": _TASK["skip"],
                }
            # 各検鏡から分化あり分岐（定植時期発行＋後続スキップ）。常時存在。
            transitions[f"T_ari{seq}"] = {
                "name": f"分化あり{seq}", "pre": [f"P_judge{seq}"], "post": ["P_ready"],
                "enabled_when": [], "deposits": ["timing_token"],
                "knowledge_table": _KT["ari"], "task_id": _TASK["ari"],
            }
        if seqs:
            last = seqs[-1]
            # 最終検鏡から強制発行（分化なくとも定植時期を確定）。常時存在。
            transitions["T_force"] = {
                "name": "強制発行", "pre": [f"P_judge{last}"], "post": ["P_ready"],
                "enabled_when": [], "deposits": ["timing_token"],
                "knowledge_table": _KT["force"], "task_id": _TASK["force"],
            }
        # 各準備トランジションは P_ready（定植前）に 1 トークンずつ deposit する。
        # deposits: このトランジションが P_ready に置くトークン（表示整合用の発生源）。
        # マルチ選定は熱消毒完了（P_heat_done の heat_token）が前提: 熱消毒が発行する
        # heat_token がマルチ選定のプレプレースに接続される（熱消毒完了→マルチ選定のゲート）。
        #
        # マルチ3ジョブ（防除＝薬剤選定→在庫確認→散布 と同型・§12.7）:
        #   T_mulch_select（マルチ選定）  : 認知=ベッド台帳(beds)・決定=資材DB(mulch_materials)
        #   T_mulch_inv   （マルチ在庫確認）: 評価・緒言=在庫(mulch_inventory)
        #   T_multilay    （マルチ敷設）    : 投射=手順書テンプレート合成・作動=敷設通知(Slack)
        # 直列チェーン: 熱消毒完了→選定→在庫確認→敷設→P_ready(multilay_token)→定植。
        transitions["T_mulch_select"] = {"name": "マルチ選定", "pre": ["P_heat_done"], "post": ["P_mulch_select"], "enabled_when": [], "deposits": ["mulch_select_token"], "knowledge_table": "rbp_perception_axis", "task_id": _TASK["mulch_select"]}
        transitions["T_mulch_inv"] = {"name": "マルチ在庫確認", "pre": ["P_mulch_select"], "post": ["P_mulch_inv"], "enabled_when": [], "deposits": ["mulch_inv_token"], "knowledge_table": "rbp_eval_box", "task_id": _TASK["mulch_inv"]}
        # マルチ敷設は在庫確認（P_mulch_inv）が揃って発火。要求（mulch_req_token）は
        # 独立プレースを持たず **P_heat_ready（熱消毒準備・T_heat の直前プレース・カラー付き）**
        # に接続されるため（2026-09-16）、要求＋両準備が揃うと T_heat（熱消毒）が発火し、
        # 熱消毒完了→選定→在庫確認→敷設が順に有効化される（要求は熱消毒の直前ゲートを効かせる）。
        transitions["T_multilay"] = {"name": "マルチ敷設", "pre": ["P_mulch_inv"], "post": ["P_ready"], "enabled_when": [], "deposits": ["multilay_token"], "knowledge_table": "rbp_projection", "task_id": _TASK["multilay"]}
        transitions["T_prep"] = {"name": "薬剤準備", "pre": [], "post": ["P_ready"], "enabled_when": [], "deposits": ["prep_token"], "knowledge_table": _KT["prep"], "task_id": _TASK["prep"]}
        transitions["T_gloves"] = {"name": "手袋揃い", "pre": [], "post": ["P_ready"], "enabled_when": [], "deposits": ["gloves_token"], "knowledge_table": _KT["prep"], "task_id": _TASK["gloves"]}
        # 熱消毒サブネット（ベッド準備）: 定植の1か月前に着手し3週間以上実施する前段。
        #   事業主体（三果倉）のマルチ敷設要求（mulch_req_token）が P_heat_ready（熱消毒
        #   準備・T_heat の直前プレース）に接続される（オブジェクト接地 §4.1・2026-09-16）。
        #   T_mulch（透明マルチ敷設）  → P_heat_ready に mulch_token
        #   T_irrigation（潅水調整）    → P_heat_ready に irrigation_token
        #   要求＋両準備が揃う → T_heat（熱消毒）発火 → P_heat_done（熱消毒完了）
        #   P_heat_done の heat_token はマルチ選定（T_mulch_select）のプレプレースに接続され、
        #   マルチ3ジョブを有効化する（熱消毒完了 → マルチ選定 → … → 定植）。
        transitions["T_mulch"] = {"name": "透明マルチ敷設", "pre": [], "post": ["P_heat_ready"], "enabled_when": [], "deposits": ["mulch_token"], "knowledge_table": _KT["prep"], "task_id": _TASK["mulch"]}
        transitions["T_irrigation"] = {"name": "潅水調整", "pre": [], "post": ["P_heat_ready"], "enabled_when": [], "deposits": ["irrigation_token"], "knowledge_table": _KT["prep"], "task_id": _TASK["irrigation"]}
        # T_heat（熱消毒）は pre=P_heat_ready の全トークン（要求＋透明マルチ敷設＋潅水調整）
        # が揃うと発火（AND-JOIN）。要求（mulch_req_token）が熱消毒の直前ゲートを効かせる。
        transitions["T_heat"] = {"name": "熱消毒", "pre": ["P_heat_ready"], "post": ["P_heat_done"],
                                 "enabled_when": ["mulch_req_token", "mulch_token", "irrigation_token"], "deposits": ["heat_token"], "knowledge_table": _KT["prep"], "task_id": _TASK["heat"]}
        # 定植: 唯一のプレプレース P_ready に全必須トークンが揃うと発火（AND-JOIN）。
        # 熱消毒（heat_token）は定植の直接条件ではなく、マルチ敷設の前提（pre）として
        # 間接に効く（熱消毒→マルチ敷設→定植）。
        transitions["T_transplant"] = {
            "name": "定植",
            "pre": ["P_ready"],
            "post": ["P_transplant"],
            "enabled_when": ["timing_token", "multilay_token", "prep_token", "gloves_token"],
            "knowledge_table": _KT["prep"], "task_id": _TASK["transplant"],
        }
        # ---- 防除3ジョブ（薬剤選定→在庫確認→散布、マルチ敷設と同型の直列チェーン） ----
        # 防除（薬剤選定）は1つの業務（task_id='rx-select'・5/5 接地済み）で、認知→評価→
        # 決定→投射→作動は**各作業ステップ（トランジション）の内部ナレッジ**（クリックで
        # 5段ダイアログ）。マルチ敷設（選定→在庫→敷設）と同型に分解し、トップではマクロ
        # （防除）に折りたたみ、ドリルダウンで 薬剤選定→在庫確認→散布 のネットを表示する。
        # 各ステップは task_id='rx-select' を共有（同一業務の5段ナレッジ）。
        # コホートネットと因果的に独立（病害虫予測で起動）なのでコホートのプレースを
        # 参照しない独立サブネット（pre/post は内部プレースのみ）。
        transitions["T_rx_select"] = {"name": "薬剤選定", "pre": ["P_rx_req"], "post": ["P_rx_select"], "enabled_when": [], "deposits": ["rx_select_token"], "knowledge_table": "rbp_perception_axis", "task_id": "rx-select"}
        transitions["T_rx_inv"] = {"name": "在庫確認", "pre": ["P_rx_select"], "post": ["P_rx_inv"], "enabled_when": [], "deposits": ["rx_inv_token"], "knowledge_table": "rbp_eval_box", "task_id": "rx-select"}
        transitions["T_rx_spray"] = {"name": "散布", "pre": ["P_rx_inv"], "post": [], "enabled_when": [], "deposits": [], "knowledge_table": "rbp_projection", "task_id": "rx-select"}

        # ---- エッジ（トランジション間の有向弧。BFS レイアウト用） ----
        # 検鏡鎖を直列、分化ありは定植時期発行＋後続スキップ、準備は検鏡1から分岐、
        # 定植は全発火源（分化あり/強制発行/準備3）に収束。
        edges = []
        for a, b in zip(seqs, seqs[1:]):
            edges.append([f"T_judge{a}", f"T_judge{b}"])
            edges.append([f"T_skip{a}", f"T_judge{b}"])
        # 分化あり分岐は常時存在: T_ari{seq}→T_skip{seq}（後続スキップ）、
        # T_ari{seq}→T_transplant（定植時期発行で収束）。
        for seq in seqs:
            if seq < seqs[-1]:
                edges.append([f"T_ari{seq}", f"T_skip{seq}"])
            edges.append([f"T_ari{seq}", "T_transplant"])
        if seqs:
            last = seqs[-1]
            edges.append([f"T_judge{last}", "T_force"])  # 最終→強制発行（常時）
            edges.append(["T_force", "T_transplant"])
            first = f"T_judge{seqs[0]}"
            # 薬剤準備/手袋揃いは入口（検鏡1）から分岐（マルチ敷設は熱消毒完了が前提なので
            # 検鏡1からの直接分岐ではなく、熱消毒→マルチ敷設の経路で有効化される）。
            for t in ("T_prep", "T_gloves"):
                edges.append([first, t])
            # 熱消毒サブネット: 入口（検鏡1）から透明マルチ敷設/潅水調整へ分岐。
            edges.append([first, "T_mulch"])
            edges.append([first, "T_irrigation"])
            edges.append([first, "T_transplant"])
        # 熱消毒サブネットの内部弧: 透明マルチ敷設/潅水調整 → 熱消毒 → マルチ選定。
        # 熱消毒が発行する heat_token（P_heat_done）がマルチ選定のプレプレースに接続され、
        # マルチ3ジョブ（選定→在庫確認→敷設）を有効化する（熱消毒→マルチ選定→…→定植）。
        edges.append(["T_mulch", "T_heat"])
        edges.append(["T_irrigation", "T_heat"])
        edges.append(["T_heat", "T_mulch_select"])
        # マルチ3ジョブの直列弧（防除同型）。
        edges.append(["T_mulch_select", "T_mulch_inv"])
        edges.append(["T_mulch_inv", "T_multilay"])
        # 防除3ジョブの直列弧（薬剤選定→在庫確認→散布）。独立サブネット（コホートの
        # プレースを参照しない）なので、コホートのエッジとは接続しない。
        edges.append(["T_rx_select", "T_rx_inv"])
        edges.append(["T_rx_inv", "T_rx_spray"])
        for t in ("T_multilay", "T_prep", "T_gloves"):
            edges.append([t, "T_transplant"])
        entry = f"T_judge{seqs[0]}" if seqs else "T_transplant"

        # ---- プレースが保持する token_key 集合（単一 or カラームルチ） ----
        def _tks(pid):
            pl = places[pid]
            if "colors" in pl:
                return [c["token_key"] for c in pl["colors"]]
            return [pl["token_key"]] if pl.get("token_key") else []

        # ---- 発火可能判定（真の Petri 網発火ルール: pre 全プレースの全トークン有無） ----
        def _has(pid):
            return all(marking.get(tk) not in (None, "") for tk in _tks(pid))
        firing = {tid: all(_has(p) for p in tr["pre"]) if tr["pre"] else False
                  for tid, tr in transitions.items()}

        # ---- current_places: token_key→値（カラープレースは全 colors を平坦化） ----
        current_places = {}
        for pid, pl in places.items():
            for tk in _tks(pid):
                current_places[tk] = marking.get(tk)

        # ---- 階層（ドリルダウン）: サブネットをマクロノードに折りたたむ ----
        # トップレベル（折りたたみ）では各サブネットの内部構造を隠蔽し、マクロ
        # トランジションで表現する。クリックで内部サブネット（internal）に
        # ドリルダウンする。構造（P/T/弧）は固定のまま、表示の階層化のみ行う
        # （真のペトリネット語義は internal にそのまま保持）。
        #   検鏡マクロ（T_judge_macro）: 検鏡1→…→最終（順送り＋分化ありスキップ
        #     ＋最終強制発行）→ 定植時期トークンを P_ready に発行。
        #   熱消毒マクロ（T_heat_macro）: 透明マルチ敷設＋潅水調整 → 熱消毒 →
        #     熱消毒完了（heat_token を P_heat_done に発行）。その heat_token が
        #     マルチ敷設（T_multilay）のプレプレースに接続され、定植へ導く。
        #     定植の1か月前に着手し3週間以上実施するベッド準備の前段フェーズ。
        hierarchy = None
        if seqs:
            # ---- 検鏡マクロ ----
            MACRO_J = "T_judge_macro"
            j_trans = []
            for seq in seqs:
                j_trans.append(f"T_judge{seq}")
                j_trans.append(f"T_ari{seq}")
                if seq < seqs[-1]:
                    j_trans.append(f"T_skip{seq}")
            j_trans.append("T_force")
            j_places = [f"P_judge{seq}" for seq in seqs]
            j_internal_places = {pid: places[pid] for pid in j_places}
            # 出力先（定植前 P_ready）を文脈としてドリル表示に含める（T_ari/T_force の sink）。
            j_internal_places["P_ready"] = places["P_ready"]
            j_internal_transitions = {tid: transitions[tid] for tid in j_trans}
            j_internal_firing = {tid: firing[tid] for tid in j_trans}
            # 内部サブネットのレイアウト用エッジ（検鏡鎖の順送り＋スキップ＋最終強制発行）。
            j_internal_edges = []
            for a, b in zip(seqs, seqs[1:]):
                j_internal_edges.append([f"T_judge{a}", f"T_judge{b}"])
                j_internal_edges.append([f"T_skip{a}", f"T_judge{b}"])
            for seq in seqs:
                if seq < seqs[-1]:
                    j_internal_edges.append([f"T_ari{seq}", f"T_skip{seq}"])
            j_internal_edges.append([f"T_judge{seqs[-1]}", "T_force"])
            judge_macro = {
                "macro_id": MACRO_J,
                "macro_name": "検鏡",
                "macro_pre": [],  # 検鏡は入口（外部判定待ち・前プレースなし）
                "macro_post": ["P_ready"],
                "macro_deposits": ["timing_token"],  # 折りたたみマクロが P_ready に置くトークン
                "macro_fired": timing_determined,  # 定植時期が確定した（サブネットが完了）
                "internal_place_ids": j_places,
                "internal_transition_ids": j_trans,
                "internal": {
                    "name": f"{cname} 検鏡サブネット",
                    "places": j_internal_places,
                    "transitions": j_internal_transitions,
                    "edges": j_internal_edges,
                    "entry": f"T_judge{seqs[0]}",
                    "firing_enabled": j_internal_firing,
                    "current_places": current_places,
                },
            }

            # ---- 熱消毒マクロ ----
            MACRO_H = "T_heat_macro"
            h_trans = ["T_mulch", "T_irrigation", "T_heat"]
            h_places = ["P_heat_ready", "P_heat_done"]
            h_internal_places = {pid: places[pid] for pid in h_places}
            # ドリル表示の文脈として定植前 P_ready を含める（マルチ敷設の出力先）。
            # P_heat_done（熱消毒完了・heat_token）はマルチ敷設（T_multilay）の
            # プレプレースに接続される（熱消毒→マルチ敷設のゲート）。
            h_internal_places["P_ready"] = places["P_ready"]
            h_internal_transitions = {tid: transitions[tid] for tid in h_trans}
            h_internal_firing = {tid: firing[tid] for tid in h_trans}
            h_internal_edges = [
                ["T_mulch", "T_heat"],
                ["T_irrigation", "T_heat"],
            ]
            heat_macro = {
                "macro_id": MACRO_H,
                "macro_name": "熱消毒",
                "macro_pre": ["P_heat_ready"],  # 前提（透明マルチ敷設＋潅水調整が揃う準備プレース）
                "macro_post": ["P_heat_done"],  # 熱消毒が発行する heat_token の置き先（マルチ敷設の前提）
                "macro_deposits": ["heat_token"],  # 折りたたみマクロが P_heat_done に置くトークン
                "macro_fired": bool(marking.get("heat_token")),  # 熱消毒完了（サブネットが完了）
                "internal_place_ids": h_places,
                "internal_transition_ids": h_trans,
                "internal": {
                    "name": f"{cname} 熱消毒サブネット（ベッド準備）",
                    "places": h_internal_places,
                    "transitions": h_internal_transitions,
                    "edges": h_internal_edges,
                    "entry": "T_mulch",
                    "firing_enabled": h_internal_firing,
                    "current_places": current_places,
                },
            }

            # ---- マルチマクロ（選定→在庫確認→敷設、防除同型の直列3ジョブ） ----
            MACRO_M = "T_mulch_macro"
            m_trans = ["T_mulch_select", "T_mulch_inv", "T_multilay"]
            # マルチサブネットの内部プレース（選定・在庫確認）。要求（mulch_req_token）は
            # 独立プレースを持たず P_heat_done（熱消毒完了・カラー付き）に接続されるため、
            # ここには含めない。P_heat_done は前提（熱消毒完了）としてドリル表示の文脈に含める。
            m_places = ["P_mulch_select", "P_mulch_inv"]
            m_internal_places = {pid: places[pid] for pid in m_places}
            # ドリル表示の文脈: 前提（熱消毒完了・要求も接続）と出力先（定植前 P_ready）。
            m_internal_places["P_heat_done"] = places["P_heat_done"]
            m_internal_places["P_ready"] = places["P_ready"]
            m_internal_transitions = {tid: transitions[tid] for tid in m_trans}
            m_internal_firing = {tid: firing[tid] for tid in m_trans}
            m_internal_edges = [
                ["T_mulch_select", "T_mulch_inv"],
                ["T_mulch_inv", "T_multilay"],
            ]
            mulch_macro = {
                "macro_id": MACRO_M,
                "macro_name": "マルチ敷設（選定→在庫→敷設）",
                # 前提 = P_heat_done（熱消毒完了・カラー付き）。heat_token（熱消毒→マルチ敷設
                # のゲート）と、物理界のドメインオブジェクト（本舗）がスケジューラ発火で置く
                # 要求トークン（mulch_req_token）の**両方がこの1つのプレース**に接続される
                # （§4.1: オブジェクトは必ずプレースに接続・ユーザー指定 2026-09-07: 熱消毒完了
                # と同じプレース）。折りたたみ表示でマクロの入口（macro_pre）として接続し、
                # 膜弧の終点が宙に浮かないようにする（内部の T_multilay が隠れても P_heat_done→
                # マクロの弧で接続されたまま見える）。
                "macro_pre": ["P_heat_done"],
                "macro_post": ["P_ready"],  # 敷設が multilay_token を置く先
                "macro_deposits": ["multilay_token"],
                "macro_fired": bool(marking.get("multilay_token")),  # 敷設完了
                "internal_place_ids": m_places,
                "internal_transition_ids": m_trans,
                "internal": {
                    "name": f"{cname} マルチサブネット（選定→在庫確認→敷設）",
                    "places": m_internal_places,
                    "transitions": m_internal_transitions,
                    "edges": m_internal_edges,
                    "entry": "T_mulch_select",
                    "firing_enabled": m_internal_firing,
                    "current_places": current_places,
                },
            }

            # ---- 折りたたみ表示のレイアウト用エッジ（マクロが入口に置換） ----
            # 検鏡マクロ → 準備2（薬剤準備/手袋揃い）→ 定植
            # 熱消毒マクロ → マルチマクロ（選定→在庫→敷設）→ 定植
            collapsed_edges = []
            for t in ("T_prep", "T_gloves"):
                collapsed_edges.append([MACRO_J, t])
                collapsed_edges.append([t, "T_transplant"])
            collapsed_edges.append([MACRO_J, "T_transplant"])
            collapsed_edges.append([MACRO_H, MACRO_M])
            collapsed_edges.append([MACRO_M, "T_transplant"])

            # ---- 防除マクロ（薬剤選定→在庫確認→散布、マルチ敷設と同型の直列3ジョブ） ----
            # 防除（薬剤選定 = 旧「固定 STN」= jobnet.NET）はマルチ敷設（選定→在庫→敷設）
            # と同型の**マクロ**として表現する。トップではマクロ（防除）に折りたたみ、
            # ドリルダウンで 薬剤選定→在庫確認→散布 のネットを表示し、各ステップ
            # （T_rx_select 等・task_id='rx-select'）をクリックすると**認知→評価→決定→
            # 投射→作動の5段ナレッジ**がダイアログで出る（rx-select は 5/5 接地済み）。
            # 認知→…→作動はネット構造ではなく各作業ステップの内部ナレッジ（マルチ敷設の
            # リーフトランジション T_mulch_select 等と同一パターン）。
            # コホートネットのプレースを参照しない独立サブネット（病害虫予測で起動）のため、
            # collapsed 表示では孤立マクロとして現れる（熱消毒/マルチと同型・弧なし）。
            MACRO_RX = "T_rx_macro"
            rx_trans = ["T_rx_select", "T_rx_inv", "T_rx_spray"]
            # P_rx_req（防除要求）= 防除サブネットの入口ゲート（T_rx_select の pre）。
            # ドリルダウン表示で育苗圃 → P_rx_req の膜弧の終点として見えるように
            # internal に含める（膜=接地の点・内部構造ではないが入口ゲートとして表示）。
            rx_places = ["P_rx_req", "P_rx_select", "P_rx_inv"]
            rx_internal_places = {pid: places[pid] for pid in rx_places}
            rx_internal_transitions = {tid: transitions[tid] for tid in rx_trans}
            rx_internal_firing = {tid: firing[tid] for tid in rx_trans}
            rx_internal_edges = [
                ["T_rx_select", "T_rx_inv"],
                ["T_rx_inv", "T_rx_spray"],
            ]
            rx_macro = {
                "macro_id": MACRO_RX,
                "macro_name": "防除（選定→在庫→散布）",
                # 前提 = P_rx_req（防除要求）。育苗圃 が防除暦（spray_schedule）発火で
                # 置く要求トークン（rx_req_token）が接続される入口ゲート（T_rx_select の pre）。
                # マルチ敷設（macro_pre=["P_heat_done"]）と同型: 折りたたみ表示でマクロの入口
                # （macro_pre）として接続し、育苗圃 → P_rx_req の膜弧の終点が宙に浮かない
                # ようにする（内部の T_rx_select が隠れても P_rx_req→マクロの弧で接続されたまま）。
                "macro_pre": ["P_rx_req"],
                "macro_post": [],
                "macro_deposits": [],
                "macro_fired": bool(marking.get("rx_select_token")),  # 薬剤選定が着手
                "internal_place_ids": rx_places,
                "internal_transition_ids": rx_trans,
                "internal": {
                    "name": f"{cname} 防除サブネット（薬剤選定→在庫確認→散布）",
                    "places": rx_internal_places,
                    "transitions": rx_internal_transitions,
                    "edges": rx_internal_edges,
                    "entry": "T_rx_select",
                    "firing_enabled": rx_internal_firing,
                    "current_places": current_places,
                },
            }
            # hierarchy はトップレベルで検鏡マクロを保持（後方互換: 従来フィールド）
            # ＋ macros リスト（検鏡＋熱消毒＋マルチ＋防除）で4マクロを表現する。
            hierarchy = dict(judge_macro)
            hierarchy["macros"] = [judge_macro, heat_macro, mulch_macro, rx_macro]
            hierarchy["collapsed"] = {"edges": collapsed_edges, "entry": MACRO_J}

        return {
            # STB ドメインの STN 統合（ts/STBドメインSTN統合設計.md）: 「固定 STN」と
            # 「第5期」は 1 つ。収穫年（第5期/第6期）はネット名でなく、育苗バッチ
            # （コホート）のデータ属性。ネット名は収穫年に依存しない「STB ドメインの
            # STN」で固定し、収穫年ラベルは cohort_name（第5期）としてデータ属性で保持。
            "name": "STB ドメインの STN",
            "model": "petri_net_cohort",
            "cohort_id": cid,
            "cohort_name": cname,
            "places": places,
            "transitions": transitions,
            "edges": edges,
            "entry": entry,
            "firing_enabled": firing,
            "current_places": current_places,
            # マルチ敷設要求（オブジェクト接地・§4.1・事業主体 §5）: 事業主体（三果倉）
            # のスケジュール発火で P_heat_done（熱消毒完了・カラー付き）に置かれる要求
            # トークンの元データ。膜弧の起点=主体、圃場（本圃）は文脈（token.field_name）。
            # 膜弧のトークン値・ドメインカードの圃場属性（延べ長さ・マルチ種別）に表示。
            "mulch_req": mulch_req,
            # 定植要求（オブジェクト接地・§4.1・事業主体 §5）: 事業主体（三果倉）の
            # 定植スケジュール発火で P_ready（定植前・カラー付き）に置かれる要求トークンの
            # 元データ。マルチ敷設要求と同型で、膜弧の起点=主体・圃場は文脈。
            "transplant_req": transplant_req,
            # 防除要求（オブジェクト接地・§4.1・IF 入方向・事業主体 §5）: 事業主体
            # （三果倉）の防除暦（spray_schedule）発火で P_rx_req（防除要求・
            # T_rx_select の pre・防除入口ゲート）に置かれる要求トークンの元データ。
            # マルチ/定植要求と同型で、事業主体 → P_rx_req の膜弧（起点=主体・圃場=文脈）。
            "rx_req": rx_req,
            # 普遍構造（骨格）＋稼働ゲート（§12）。コホートネットも全 T が
            # 認知→評価→決定→投射＋作動（作動=Tの正体）の具現化。
            "universal_transition": jobnet.NET["universal_transition"],
            "runnability": jobnet.net_runnability(transitions),
            **({"hierarchy": hierarchy} if hierarchy else {}),
        }

    def _handle_humos_get(self):
        """GET /api/humos — HUMOS（土壌）の記録ログ。

        正は humos.py（DB 永続・共有 OS humos_os へのラッパー、net_id="stb"）。
        langgraph_designer.html の「HUMOS ログ」パネルが取得して表示する:
          - marking     : 現在のマーキング（M、token_key → 値）
          - firing_log  : 発火ログ（{tid, consumed, generated, ts} の時系列）
          - sos_log     : 作動ログ（{action, result, ts} の時系列）
          - replay_ok   : マーキング ≡ M0 からのリプレイ（TS設計 §7.5 整合検証）
        """
        marking = humos.get_marking()
        replayed = humos.replay_marking()
        # 整合: リプレイ（M0＋発火ログ再生）が現在のマーキングと一致するか
        replay_ok = all(
            replayed.get(k) == v for k, v in marking.items()
        ) and all(
            marking.get(k) == v for k, v in replayed.items()
        )
        self._send_json(200, {
            "marking": marking,
            "firing_log": humos.get_firing_log(),
            "sos_log": humos.get_sos_log(),
            "writing_log": humos.get_writing_log(),
            "replay_ok": replay_ok,
        })

    def _handle_humos_meta_get(self):
        """GET /api/humos/meta — HUMOS OS コンソール（humos.html）のメタ情報。

        ドメイン非依存の OS レベル情報（真界_HUMOS管理画面設計.md §4.2-B）。
        「不変（エンジン）」と「可変（ナレッジ）」を分離して返す:
          - os     : OS バージョン・モジュール構成（不変・エンジン）
          - domain : このサーバーの domain_id / net_id（どのドメインか）
          - counts : このドメインに差し込まれた量（可変・ナレッジ）
          - replay_ok: マーキング ≡ M0 リプレイ（OS の健全性の根拠）
        """
        import humos_os
        from humos_os import entity as _entity
        conn = get_db()
        # 不変（エンジン）: OS バージョン・モジュール構成
        os_info = {
            "name": "HUMOS",
            "version": getattr(humos_os, "__version__", "unknown"),
            "modules": ["humos", "stn", "plan", "entity", "schema"],
        }
        # 可変（ナレッジ）: このドメインに差し込まれた量
        try:
            entity_rows = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
        except sqlite3.OperationalError:
            entity_rows = 0
        try:
            comp_rows = conn.execute("SELECT COUNT(*) FROM object_component").fetchone()[0]
        except sqlite3.OperationalError:
            comp_rows = 0
        # RBP タスク（5段接地）
        rbp_tasks = 0
        rbp_tasks_grounded = 0
        try:
            rbp_rows = conn.execute("SELECT task_id FROM rbp_task").fetchall()
            rbp_tasks = len(rbp_rows)
            # 接地済み = 5段（認知/評価/決定/投射/作動）が揃ったタスク
            for (task_id,) in rbp_rows:
                stages = 0
                for tbl in ("rbp_perception_axis", "rbp_eval_box",
                            "rbp_spec_condition", "rbp_projection", "rbp_actuation"):
                    try:
                        n = conn.execute(
                            f"SELECT COUNT(*) FROM {tbl} WHERE task_id=?", (task_id,)
                        ).fetchone()[0]
                    except sqlite3.OperationalError:
                        n = 0
                    if n > 0:
                        stages += 1
                if stages >= 5:
                    rbp_tasks_grounded += 1
        except sqlite3.OperationalError:
            pass
        # schedule def（PLAN レイヤー）
        try:
            sched_defs = conn.execute("SELECT COUNT(*) FROM schedule_def").fetchone()[0]
        except sqlite3.OperationalError:
            sched_defs = 0
        # STN 構造（不変構造: place / transition 数）
        net = jobnet.NET
        places_n = len(net.get("places", {}))
        transitions_n = len(net.get("transitions", {}))
        conn.close()
        # 健全性の根拠
        marking = humos.get_marking()
        replayed = humos.replay_marking()
        replay_ok = all(
            replayed.get(k) == v for k, v in marking.items()
        ) and all(
            marking.get(k) == v for k, v in replayed.items()
        )
        self._send_json(200, {
            "os": os_info,
            "domain": {
                "subject_id": "mikakura",
                "domain_id": "iichigo",
                "net_id": humos.get_net_id(),
            },
            "counts": {
                "entities": entity_rows,
                "components": comp_rows,
                "rbp_tasks": rbp_tasks,
                "rbp_tasks_grounded": rbp_tasks_grounded,
                "schedule_defs": sched_defs,
                "places": places_n,
                "transitions": transitions_n,
            },
            "replay_ok": replay_ok,
        })

    # ─── 運航管理センター（operations）API ────────────────────────
    # PLAN レイヤー（humos_os.plan）を HTTP に露出。UI（石6・operations.html）の土台。
    # 異常 = Expected State(T) ≠ Actual State(T)。

    def _actual_state(self) -> str:
        """現在の状態（STN の place_id）をマーキングから導出する。

        現在トークンを持つプレースのうち、後段（store != "humos"）の最後の
        プレースを「現在到達している状態」とみなす。後段が無い（実行前）なら
        None（未到達扱い）。
        """
        marking = humos.get_marking()  # token_key → 値
        last = None
        for pid, p in jobnet.NET["places"].items():
            tk = p.get("token_key")
            if not tk or p.get("store") == "humos":
                continue
            if marking.get(tk) not in (None, "", []):
                last = pid  # 後段の順に上書き → 最後の到達プレース
        return last

    def _spray_instances(self, conn, now) -> list:
        """防除暦（spray_schedule）を operations の「業務インスタンス」に変換する。

        設計 §2.3: 防除カレンダーは schedule_def の特殊ケース。
          cadence = interval（散布間隔）
          expected_state = P_inv_result（散布完了）
          deadline = 散布予定日
        完了判定（ユーザー指定）: 実績のみ（actual_date 設定 or status が
        done 系）。実績なしの過去予定は期限超過（遅延・赤）＝「散布未実施」。
        散布予定日を月ごとに束ねる（group_id=sp-YYYY-MM）→ ①運行図表が
        散布鎖（散布間隔）として描画する。
        """
        from datetime import datetime
        field_names = {r["id"]: r["name"]
                       for r in conn.execute("SELECT id, name FROM fields").fetchall()}
        done_statuses = {"done", "completed", "finished", "actual"}
        out = []
        rows = conn.execute(
            "SELECT id, schedule_date, actual_date, status, notes, field_id "
            "FROM spray_schedule WHERE schedule_date IS NOT NULL "
            "ORDER BY schedule_date"
        ).fetchall()
        for r in rows:
            sd = r["schedule_date"]
            try:
                D = datetime.strptime(sd, "%Y-%m-%d")
            except ValueError:
                continue  # 不正日付（例: 2026-02-29）はスキップ
            ad = r["actual_date"]
            reached = bool(ad) or (r["status"] or "") in done_statuses
            if reached:
                status = "on_track"
            elif now < D:
                status = "on_track"      # 未達期（まだ散布予定日以前）
            else:
                status = "overdue"       # 期限超過（散布未実施）
            month = sd[:7]  # YYYY-MM
            fname = field_names.get(r["field_id"]) if r["field_id"] else None
            out.append({
                "def_id": f"sp-{r['id']}",
                "name": f"散布 {sd[5:]}（{r['notes'] or '防除'}）",
                "group_id": f"sp-{month}",
                "group_name": f"散布 {month}",
                "target_key": "spray",
                "target_value": fname,
                "seq": None,
                "cadence": "interval",
                "expected_state": "P_inv_result",
                "actual_state": None,
                "reached": reached,
                "status": status,
                "expected_at": f"{sd} 08:00:00",
                "deadline": f"{sd} 23:59:59",
                "slack_seconds": (D - now).total_seconds(),
                "overdue_seconds": max(0.0, (now - D).total_seconds()),
                "source": "spray",
            })
        return out

    def _transplant_instances(self, conn, now) -> list:
        """定植暦（transplant_schedule）を operations の「業務インスタンス」に変換する。

        花芽分化確認（育苗圃苗の顕微鏡検査）を暦として管理。

        **コホート（育苗バッチ）割当あり** → 設計 §2.1b の**プロジェクト**として扱う。
        検鏡1→検鏡2→…→検鏡N→定植 を oneshot マイルストーン（group_id=cohort-{id}、
        seq 順）として束ね、①運行図表がコホート鎖として描画する。
        各検鏡は外部判定待ち（P_external_wait）。判定記録（judge_date 設定 or
        status=confirmed）が reached を決める（＝外部の判断がマーキングになる、STN
        の外部イベント・間接的開き）。

        **定植マイルストーン**はブリッジが導出する（DB に書かない＝未確定に戻すと自動で
        消える）。トリガー＝判定済み検鏡のうち (分化あり) または (分化なしかつ最終) の
        seq 最小のもの。自動日付＝トリガー判定日の翌日。transplant_plan（手動）が
        schedule_date で上書き。トリガー未達なら deadline=None（undefined）。
        定植は P_transplant（合成プレース・operations.html 側で名前解決）。

        **コホート未割当**の旧来行は従来どおり月ごとに束ねる（group_id=tp-YYYY-MM、
        cadence=interval）。
        """
        from datetime import datetime, timedelta
        field_names = {r["id"]: r["name"]
                       for r in conn.execute("SELECT id, name FROM fields").fetchall()}
        cohort_info = {r["id"]: {"name": r["name"], "field_id": r["field_id"]}
                       for r in conn.execute("SELECT id, name, field_id FROM cohorts").fetchall()}
        worker_names = {r["id"]: r["name"]
                        for r in conn.execute("SELECT id, name FROM workers").fetchall()}
        out = []
        rows = conn.execute(
            "SELECT id, schedule_date, start_time, end_time, judge_date, judge_result, "
            "judge_org, judge_location, status, notes, field_id, "
            "cohort_id, seq, is_final, assignee_id "
            "FROM transplant_schedule WHERE schedule_date IS NOT NULL "
            "ORDER BY schedule_date"
        ).fetchall()

        # コホートごとの検鏡を seq で束ねる（seq 未設定は日付順で補完）。
        cohort_insp = {}  # cohort_id -> list[(seq, row)]
        for r in rows:
            cid = r["cohort_id"]
            if not cid:
                continue
            seq = r["seq"] or (len(cohort_insp.get(cid, [])) + 1)
            cohort_insp.setdefault(cid, []).append((seq, r))

        for cid, items in cohort_insp.items():
            items.sort(key=lambda x: x[0])
            info = cohort_info.get(cid, {})
            cname = info.get("name") or f"コホート{cid}"
            cfield = info.get("field_id")
            cfname = field_names.get(cfield) if cfield else None

            # 各検鏡マイルストーン（P_external_wait・oneshot）。
            for seq, r in items:
                sd = r["schedule_date"]
                try:
                    D = datetime.strptime(sd, "%Y-%m-%d")
                except ValueError:
                    continue
                reached = bool(r["judge_date"]) or (r["status"] or "") == "confirmed"
                status = "on_track" if (reached or now < D) else "overdue"
                org = r["judge_org"] or "那賀地方いちご生産組合連合会"
                name = f"検鏡{seq}"
                if r["judge_result"]:
                    name += f"（{r['judge_result']}）"
                if r["is_final"]:
                    name += "・最終"
                start = r["start_time"] or "08:00"
                out.append({
                    "def_id": f"tp-{r['id']}",
                    "name": f"{name} {sd[5:]}",
                    "group_id": f"cohort-{cid}",
                    "group_name": cname,
                    "target_key": "transplant",
                    "target_value": cfname or org,
                    "seq": seq,
                    "cadence": "oneshot",
                    "expected_state": "P_external_wait",
                    "actual_state": None,
                    "reached": reached,
                    "status": status,
                    "expected_at": f"{sd} {start}:00",
                    "deadline": f"{sd} 23:59:59",
                    "slack_seconds": (D - now).total_seconds(),
                    "overdue_seconds": max(0.0, (now - D).total_seconds()),
                    "source": "transplant",
                    "judge_org": org,
                    "judge_result": r["judge_result"],
                    "judge_date": r["judge_date"],
                    "judge_location": r["judge_location"],
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                    "assignee": worker_names.get(r["assignee_id"]),
                })

            # 定植マイルストーン導出（トリガー判定 → 翌日、transplant_plan で上書き）。
            max_seq = max(s for s, _ in items)
            trigger = None
            for seq, r in items:  # seq 昇順 → 最初のトリガーで止まる
                res = (r["judge_result"] or "").strip()
                judged = bool(r["judge_date"]) or (r["status"] or "") == "confirmed"
                if not judged:
                    continue
                if "あり" in res or ("なし" in res and r["is_final"]):
                    trigger = r
                    break
            plan_row = conn.execute(
                "SELECT * FROM transplant_plan WHERE cohort_id=?", (cid,)).fetchone()
            auto_date = None
            if trigger is not None:
                try:
                    auto_date = (datetime.strptime(trigger["judge_date"], "%Y-%m-%d")
                                 + timedelta(days=1)).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    auto_date = None
            t_date = (plan_row["schedule_date"] if plan_row else None) or auto_date
            t_reached = bool(plan_row and plan_row["status"] == "done")
            TD = None
            if t_date:
                try:
                    TD = datetime.strptime(t_date, "%Y-%m-%d")
                except ValueError:
                    TD = None
            if t_reached:
                t_status = "on_track"
            elif TD is not None:
                t_status = "on_track" if now < TD else "overdue"
            else:
                t_status = "undefined"
            t_assignee = None
            if plan_row:
                t_assignee = worker_names.get(plan_row["assignee_id"])
            out.append({
                "def_id": f"tp-plan-{cid}",
                "name": f"定植 {t_date[5:]}" if t_date else "定植",
                "group_id": f"cohort-{cid}",
                "group_name": cname,
                "target_key": "transplant",
                "target_value": cfname or "定植",
                "seq": max_seq + 1,
                "cadence": "oneshot",
                "expected_state": "P_transplant",
                "actual_state": None,
                "reached": t_reached,
                "status": t_status,
                "expected_at": f"{t_date} 08:00:00" if t_date else None,
                "deadline": f"{t_date} 23:59:59" if t_date else None,
                "slack_seconds": (TD - now).total_seconds() if TD else None,
                "overdue_seconds": max(0.0, (TD - now).total_seconds()) if TD else 0.0,
                "source": "transplant_plan",
                "auto_date": auto_date,
                "trigger_date": (trigger["judge_date"] if trigger else None),
                "assignee": t_assignee,
            })

        # コホート未割当の旧来行（月束ね・interval）は従来どおり。
        for r in rows:
            if r["cohort_id"]:
                continue
            sd = r["schedule_date"]
            try:
                D = datetime.strptime(sd, "%Y-%m-%d")
            except ValueError:
                continue  # 不正日付はスキップ
            reached = bool(r["judge_date"]) or (r["status"] or "") == "confirmed"
            if reached:
                status = "on_track"
            elif now < D:
                status = "on_track"      # 未達期（まだ確認予定日以前）
            else:
                status = "overdue"       # 期限超過（確認未実施）
            month = sd[:7]  # YYYY-MM
            fname = field_names.get(r["field_id"]) if r["field_id"] else None
            org = r["judge_org"] or "那賀地方いちご生産組合連合会"
            name = "花芽分化確認"
            if r["judge_result"]:
                name += f"（{r['judge_result']}）"
            start = r["start_time"] or "08:00"
            out.append({
                "def_id": f"tp-{r['id']}",
                "name": f"{name} {sd[5:]}",
                "group_id": f"tp-{month}",
                "group_name": f"定植 {month}",
                "target_key": "transplant",
                "target_value": fname or org,
                "seq": None,
                "cadence": "interval",
                "expected_state": "P_external_wait",
                "actual_state": None,
                "reached": reached,
                "status": status,
                "expected_at": f"{sd} {start}:00",
                "deadline": f"{sd} 23:59:59",
                "slack_seconds": (D - now).total_seconds(),
                "overdue_seconds": max(0.0, (now - D).total_seconds()),
                "source": "transplant",
                "judge_org": org,
                "judge_result": r["judge_result"],
                "judge_date": r["judge_date"],
                "judge_location": r["judge_location"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
            })
        return out

    def _handle_operations_status(self):
        """GET /api/operations/status — 全業務定義の期待状態・逸脱判定。

        PLAN（schedule_def）× 実状態（マーキング）× 現在時刻 を突き合わせ、
        運行図表（①）・アラートフィード（④）が食うデータ。
        防除暦（spray_schedule）も §2.3 に従い業務インスタンスとしてマージする。
        """
        from datetime import datetime
        conn = humos.get_conn()
        plan.ensure_plan_schema(conn)
        now = datetime.now()
        actual = self._actual_state()
        rows = plan.expected_state_at(conn, humos.get_net_id(), now=now,
                                      actual_state=actual)
        rows = rows + self._spray_instances(conn, now)
        rows = rows + self._transplant_instances(conn, now)
        self._send_json(200, {
            "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            "net_id": humos.get_net_id(),
            "actual_state": actual,
            "instances": rows,
            "summary": {k: sum(1 for r in rows if r["status"] == k)
                        for k in ("on_track", "deviating", "overdue", "undefined")},
        })

    def _handle_operations_alerts(self):
        """GET /api/operations/alerts — 逸脱中（deviating/overdue）のアラート一覧。

        ③ 異常解釈パネルの「決定論的部分」。AI 解釈（/api/operations/ai-interpret）
        はこの一覧を食って自然言語化する。
        """
        from datetime import datetime
        conn = humos.get_conn()
        plan.ensure_plan_schema(conn)
        now = datetime.now()
        actual = self._actual_state()
        rows = plan.expected_state_at(conn, humos.get_net_id(),
                                      now=now, actual_state=actual)
        rows = rows + self._spray_instances(conn, now)
        rows = rows + self._transplant_instances(conn, now)
        alerts = [r for r in rows if r["status"] in ("deviating", "overdue")]
        # 優先度: overdue（期限超過）→ deviating（潜在異常）。期限の近い順。
        alerts.sort(key=lambda r: (0 if r["status"] == "overdue" else 1,
                                   r["deadline"] or ""))
        self._send_json(200, {
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actual_state": actual,
            "count": len(alerts),
            "alerts": alerts,
        })

    def _handle_operations_timeline(self):
        """GET /api/operations/timeline — 発火履歴（時刻付き）＋作動ログ。

        ① 運行図表の「実際線」（各状態にいつ到達したか）のデータ源。
        firing_log（発火 ts）＋ sos_log（作動 ts）を時系列で返す。
        """
        self._send_json(200, {
            "firing_log": humos.get_firing_log(),
            "sos_log": humos.get_sos_log(),
            "marking": humos.get_marking(),
        })

    def _handle_operations_defs_get(self):
        """GET /api/operations/defs — 業務定義（schedule_def）一覧。

        登録 UI（§2.4）の表示用。?group_id= でフィルタ可。
        """
        conn = humos.get_conn()
        plan.ensure_plan_schema(conn)
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        group_id = qs.get("group_id", [None])[0]
        defs = plan.list_schedule_defs(conn, net_id=humos.get_net_id(),
                                       group_id=group_id, active_only=False)
        self._send_json(200, {"defs": defs})

    def _handle_operations_defs_post(self, body):
        """POST /api/operations/defs — 業務定義を 1 行登録。

        body: {def_id, name, target_key, target_value?, group_id?, group_name?,
               seq?, cadence, recurrence?, deadline_spec, expected_state,
               grace_seconds?, responsible_role_id?}
        responsible_role_id: 責務担当役割（role object_id・§2.10）。NULL=主体の事業主。
        """
        required = ("def_id", "name", "target_key", "cadence",
                    "deadline_spec", "expected_state")
        missing = [k for k in required if not body.get(k)]
        if missing:
            self._send_json(400, {"error": f"missing: {missing}"})
            return
        conn = humos.get_conn()
        plan.ensure_plan_schema(conn)
        try:
            plan.add_schedule_def(
                conn,
                def_id=body["def_id"],
                net_id=humos.get_net_id(),
                name=body["name"],
                target_key=body["target_key"],
                expected_state=body["expected_state"],
                cadence=body["cadence"],
                deadline_spec=body["deadline_spec"],
                target_value=body.get("target_value"),
                group_id=body.get("group_id"),
                group_name=body.get("group_name"),
                seq=int(body.get("seq", 1)),
                recurrence=body.get("recurrence") or {},
                grace_seconds=int(body.get("grace_seconds", 0)),
                responsible_role_id=body.get("responsible_role_id"),
            )
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"ok": True, "def_id": body["def_id"]})

    def _handle_operations_defs_delete(self, body):
        """POST /api/operations/defs/delete — 業務定義を削除。"""
        def_id = (body.get("def_id") or "").strip()
        if not def_id:
            self._send_json(400, {"error": "def_id is required"})
            return
        conn = humos.get_conn()
        plan.ensure_plan_schema(conn)
        plan.delete_schedule_def(conn, def_id)
        self._send_json(200, {"ok": True, "def_id": def_id})

    def _handle_operations_ai_interpret(self, body):
        """POST /api/operations/ai-interpret — 逸脱異常の 5 層自然言語解読。

        body: {alerts: [...], context: {firing_log, sos_log, inventory?}}
        返却: {now, count, source, interpretations: [{title, description, cause,
                prediction, pattern, action, confidence}]}
        5 分 TTL の頻度制限（同一異常の再解釈を抑制・設計 §3.4）。
        """
        alerts = body.get("alerts") or []
        context = body.get("context") or {}
        if not alerts:
            self._send_json(200, {
                "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "count": 0, "source": "none", "interpretations": [],
            })
            return

        key = _ai_interpret_key(alerts)
        with _ai_interpret_lock:
            cached = _ai_interpret_cache.get(key)
            if cached and (time.monotonic() - cached[0]) < _AI_INTERPRET_TTL:
                self._send_json(200, {
                    "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "count": len(alerts), "source": "cache",
                    "interpretations": cached[1],
                })
                return

        text = _ai_interpret_call(alerts, context)
        interpretations = _extract_interpretations(text)
        source = "ai"
        if interpretations is None:
            interpretations = _ai_interpret_fallback(alerts, context)
            source = "deterministic"

        with _ai_interpret_lock:
            _ai_interpret_cache[key] = (time.monotonic(), interpretations)
        self._send_json(200, {
            "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(alerts), "source": source,
            "interpretations": interpretations,
        })

    # ─── Token Management API ─────────────────────────────────────

    def _handle_tokens_check(self):
        """GET /api/tokens/check — Current token state."""
        self._send_json(200, get_token_state())

    def _handle_tokens_set(self, body):
        """POST /api/tokens/set — Set a single token."""
        key = body.get("key", "").strip()
        value = body.get("value", "").strip()
        valid_keys = sorted(get_required_keys())
        if not key or key not in valid_keys:
            self._send_json(400, {"error": f"key must be one of: {valid_keys}"})
            return
        if not value:
            self._send_json(400, {"error": "value is required"})
            return
        result = set_token(key, value)
        if "error" in result:
            self._send_json(400, result)
            return
        self._send_json(200, result)

    def _handle_tokens_reset(self, body):
        """POST /api/tokens/reset — Reset all tokens."""
        result = reset_tokens()
        self._send_json(200, result)

    # ─── Inventory Management API ─────────────────────────────────

    def _handle_inventory_get(self):
        """GET /api/inventory — List all inventory items.
           GET /api/inventory/<id> — Get single item.
           GET /api/inventory/by-pesticide/<pid> — List by pesticide ID.
        """
        if self.path == "/api/inventory":
            conn = get_db()
            rows = conn.execute(
                """SELECT i.*, p.name AS pesticideName, p.category
                   FROM inventory i
                   LEFT JOIN pesticides p ON i.pesticideId = p.id
                   ORDER BY i.expiryDate ASC, i.createdAt DESC"""
            ).fetchall()
            conn.close()
            self._send_json(200, {"inventory": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/inventory/by-pesticide/"):
            pid = self.path.split("/")[-1]
            conn = get_db()
            rows = conn.execute(
                """SELECT i.*, p.name AS pesticideName, p.category
                   FROM inventory i
                   LEFT JOIN pesticides p ON i.pesticideId = p.id
                   WHERE i.pesticideId = ?
                   ORDER BY i.expiryDate ASC""",
                (pid,)
            ).fetchall()
            conn.close()
            self._send_json(200, {"inventory": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/inventory/"):
            inv_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute(
                """SELECT i.*, p.name AS pesticideName, p.category
                   FROM inventory i
                   LEFT JOIN pesticides p ON i.pesticideId = p.id
                   WHERE i.id = ?""",
                (inv_id,)
            ).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
            return

        self._send_json(404, {"error": "not found"})
        return

    def _handle_inventory_post(self):
        """POST /api/inventory — Create inventory item.
           POST /api/inventory/<id>/consume — Decrease quantity.
           POST /api/inventory/<id>/restock — Increase quantity.
        """
        # Special actions: consume / restock
        if self.path.endswith("/consume"):
            inv_id = self.path.split("/")[-2]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            amount = body.get("amount", 0)
            if amount <= 0:
                self._send_json(400, {"error": "amount must be positive"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
                return

            current_qty = row["quantity"]
            if amount > current_qty:
                conn.close()
                self._send_json(400, {
                    "error": f"在庫が不足しています。現在の在庫: {current_qty}",
                    "available": current_qty,
                    "requested": amount,
                })
                return

            new_qty = current_qty - amount
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE inventory SET quantity=?, updatedAt=? WHERE id=?",
                (new_qty, now, inv_id)
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "consumed", "id": inv_id, "remaining": new_qty})
            return

        if self.path.endswith("/restock"):
            inv_id = self.path.split("/")[-2]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            amount = body.get("amount", 0)
            if amount <= 0:
                self._send_json(400, {"error": "amount must be positive"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
                return

            new_qty = row["quantity"] + amount
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE inventory SET quantity=?, updatedAt=? WHERE id=?",
                (new_qty, now, inv_id)
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "restocked", "id": inv_id, "total": new_qty})
            return

        # Normal: create new inventory item
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        pesticide_id = body.get("pesticideId")
        product_name = body.get("productName")
        quantity = body.get("quantity", 0)

        if not pesticide_id or not product_name:
            self._send_json(400, {"error": "pesticideId and productName are required"})
            return

        # Verify pesticide exists
        conn = get_db()
        pest_row = conn.execute("SELECT id FROM pesticides WHERE id=?", (pesticide_id,)).fetchone()
        if pest_row is None:
            conn.close()
            self._send_json(404, {"error": f"pesticide {pesticide_id} not found"})
            return

        now = datetime.datetime.utcnow().isoformat()
        # id 自動生成: 行数ベースだと削除でIDの穴ができると
        # 同一idに衝突する（UNIQUE違反で500）。既存idをスキップする次番号を探す。
        if body.get("id"):
            inv_id = body["id"]
        else:
            existing = {r[0] for r in conn.execute("SELECT id FROM inventory").fetchall()}
            n = 1
            while f"INV-{n:04d}" in existing:
                n += 1
            inv_id = f"INV-{n:04d}"

        conn.execute(
            """INSERT INTO inventory
               (id, pesticideId, productName, lotNumber, quantity, unit,
                expiryDate, supplier, purchaseDate, notes, field_id,
                createdAt, updatedAt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inv_id,
                pesticide_id,
                product_name,
                body.get("lotNumber"),
                float(quantity),
                body.get("unit", "ml"),
                body.get("expiryDate"),
                body.get("supplier"),
                body.get("purchaseDate"),
                body.get("notes"),
                body.get("field_id"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        self._send_json(200, {"status": "created", "id": inv_id})
        return

    def _handle_inventory_put(self):
        """PUT /api/inventory/<id> — Update inventory item."""
        inv_id = self.path.split("/")[-1]
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        conn = get_db()
        row = conn.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone()
        if row is None:
            conn.close()
            self._send_json(404, {"error": f"inventory {inv_id} not found"})
            return

        # Validate pesticideId if provided
        if "pesticideId" in body:
            pest_row = conn.execute(
                "SELECT id FROM pesticides WHERE id=?", (body["pesticideId"],)
            ).fetchone()
            if pest_row is None:
                conn.close()
                self._send_json(404, {"error": f"pesticide {body['pesticideId']} not found"})
                return

        now = datetime.datetime.utcnow().isoformat()
        conn.execute(
            """UPDATE inventory SET
               pesticideId=?, productName=?, lotNumber=?, quantity=?, unit=?,
               expiryDate=?, supplier=?, purchaseDate=?, notes=?, field_id=?,
               updatedAt=?
               WHERE id=?""",
            (
                body.get("pesticideId", row["pesticideId"]),
                body.get("productName", row["productName"]),
                body.get("lotNumber", row["lotNumber"]),
                float(body.get("quantity", row["quantity"])),
                body.get("unit", row["unit"]),
                body.get("expiryDate", row["expiryDate"]),
                body.get("supplier", row["supplier"]),
                body.get("purchaseDate", row["purchaseDate"]),
                body.get("notes", row["notes"]),
                body.get("field_id", row["field_id"]),
                now,
                inv_id,
            ),
        )
        conn.commit()
        conn.close()
        self._send_json(200, {"status": "updated", "id": inv_id})
        return

    def _handle_spray_schedule_place(self, sched_id):
        """GET /api/spray_schedule/<id>/place — 発火前のプレース状態（2トークン）を**投入なし**で返す。

        UI「⚡今すぐ」の2段階フローの第一段階（プレビュー）用。
        圃場種（fields.type 由来）と認知で生成される病害虫予測行列（set_ids→BOX.vector）を
        算出して返し、プレース（state.py）への投入・処方生成・Slack 送信は**行わない**。
        実際の投入＋発行は POST .../generate（第二段階「OK」）が担う。
        """
        try:
            sys.path.insert(0, os.path.join(APP_ROOT, "scripts"))
            import rx_prescribe
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM spray_schedule WHERE id=?", (sched_id,)
            ).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"spray_schedule {sched_id} not found"})
                return
            field_id = row["field_id"]
            field_token = _build_field_type_token(field_id, conn) if field_id else None
            conn.close()

            box_vectors = rx_prescribe.load_eval_box_vectors()
            matrix_token = _build_pest_matrix_token(row, box_vectors)

            set_ids = json.loads(row["set_ids"]) if row["set_ids"] else []
            set_num = None
            for s in set_ids:
                mm = re.search(r"セット(\d+)", str(s))
                if mm:
                    set_num = int(mm.group(1))
                    break

            # 認知（第一トランジション）の OK/NG 判定 — トークンそろい + 行列チェック。
            perception = _run_perception_check(field_token, matrix_token)

            self._send_json(200, {
                "id": sched_id,
                "schedule_date": row["schedule_date"],
                "set_label": f"セット{set_num}" if set_num else "",
                "box": f"BOX-{set_num:02d}" if set_num else None,
                "field_id": field_id,
                "tokens": {
                    "field_type": field_token,
                    "pest_matrix": matrix_token,
                },
                "ready": (field_token is not None) and (matrix_token is not None),
                "perception": perception,
            })
        except Exception as e:
            self._send_json(500, {"error": f"place preview error: {e}"})

    def _handle_spray_schedule_generate(self, sched_id):
        """POST /api/spray_schedule/<id>/generate — 指定1行の処方生成を即時実行。

        UI の「⚡今すぐ」ボタン用。scripts/rx_prescribe.py の process_row を再利用し、
        RBP 実行 → spray_schedule 更新 → Slack 通知 を行う。
        """
        try:
            sys.path.insert(0, os.path.join(APP_ROOT, "scripts"))
            import rx_prescribe
            from datetime import datetime, timedelta, timezone
            jst = timezone(timedelta(hours=9))
            now = datetime.now(jst)

            conn = get_db()
            row = conn.execute(
                "SELECT * FROM spray_schedule WHERE id=?", (sched_id,)
            ).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"spray_schedule {sched_id} not found"})
                return

            box_vectors = rx_prescribe.load_eval_box_vectors()

            # ① 認知（第一トランジション）ゲート — RBP/Slack（評価以降）より**前**に判定する。
            # プレースへ投入予定の2トークンを算出し、認知で OK/NG を決める。
            fc = get_db()
            try:
                field_token = _build_field_type_token(row["field_id"], fc) if row["field_id"] else None
            finally:
                fc.close()
            matrix_token = _build_pest_matrix_token(row, box_vectors)
            perception = _run_perception_check(field_token, matrix_token)

            # ② 認知 NG → 次に進まない: 理由をログ出力して終了（RBP/Slack/投入を一切実行しない）。
            if not perception["ok"]:
                logger.warning(
                    f"[認知 NG] 発火を中断（次に進まない）: id={sched_id} "
                    f"date={row['schedule_date']} 理由={perception['reason']}"
                )
                logger.warning(
                    f"[認知 NG]   トークンのそろい: "
                    f"{'OK' if perception['presence']['ok'] else perception['presence']['reason']} | "
                    f"行列チェック: "
                    f"{'OK' if perception['vector']['ok'] else perception['vector']['reason']}"
                )
                self._send_json(422, {
                    "status": "perception_ng",
                    "id": sched_id,
                    "perception": perception,
                    "error": f"認知 NG: {perception['reason']}",
                })
                return

            # ③ 認知 OK → プレース（state.py の2トークン）へ投入する。
            # 圃場種（fields.type 由来）＋ 認知で生成した病害虫予測行列（set_ids→BOX.vector）。
            tokens = _fire_place_tokens(row["field_id"], row, box_vectors)

            # ④ 認知 OK のみ次に進む: RBP 処方生成（評価・決定・投射）→ Slack 通知（作動）。
            result = rx_prescribe.process_row(
                row, now,
                box_vectors,
                rx_prescribe.load_pesticide_meta(),
                conn,
            )
            conn.close()
            if not result["ok"]:
                self._send_json(422, {"error": result["error"]})
                return

            self._send_json(200, {
                "status": "generated",
                "id": sched_id,
                "set_label": result["set_label"],
                "pesticides": result["names"],
                "slack": "ok" if result["slack_ok"] else "failed",
                "tokens": tokens,
                "perception": perception,
            })
        except Exception as e:
            self._send_json(500, {"error": f"generate error: {e}"})

    def _handle_spray_schedule_copy_year(self):
        """POST /api/spray_schedule/copy-year — 年度複製。
           spray_schedule に fromYear のデータがあればそれを複製元にする
           （日付の年だけ置き換え、status='scheduled' にリセット）。
           なければ spray_history の fromYear データをブートストラップ元として使う
           （set_ids/pesticide_ids は空配列で作成し、手動入力を促す）。
           toYear に既存の同日エントリがある場合はスキップする。
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        from_year = body.get("fromYear")
        to_year = body.get("toYear")
        if not from_year or not to_year:
            self._send_json(400, {"error": "fromYear and toYear are required"})
            return
        from_year = str(from_year)
        to_year = str(to_year)

        conn = get_db()
        existing_target_dates = {
            r["schedule_date"] for r in conn.execute(
                "SELECT schedule_date FROM spray_schedule WHERE schedule_date LIKE ?",
                (f"{to_year}-%",),
            ).fetchall()
        }

        source_rows = conn.execute(
            "SELECT * FROM spray_schedule WHERE schedule_date LIKE ? ORDER BY schedule_date",
            (f"{from_year}-%",),
        ).fetchall()

        now = datetime.datetime.utcnow().isoformat()
        created = 0
        skipped = 0

        if source_rows:
            source = "spray_schedule"
            for r in source_rows:
                new_date = to_year + r["schedule_date"][4:]
                if new_date in existing_target_dates:
                    skipped += 1
                    continue
                conn.execute(
                    """INSERT INTO spray_schedule
                       (schedule_date, actual_date, status, trigger_type, trigger_ref,
                        eval_box_id, rb_out_json, set_ids, pesticide_ids, operator,
                        weather, notes, field_id, created_at, updated_at)
                       VALUES (?, NULL, 'scheduled', ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)""",
                    (
                        new_date,
                        r["trigger_type"],
                        str(r["id"]),
                        r["eval_box_id"],
                        r["rb_out_json"],
                        r["set_ids"],
                        r["pesticide_ids"],
                        r["notes"],
                        r["field_id"],
                        now,
                        now,
                    ),
                )
                created += 1
        else:
            source = "spray_history"
            history_rows = conn.execute(
                "SELECT * FROM spray_history WHERE date LIKE ? ORDER BY date",
                (f"{from_year}-%",),
            ).fetchall()
            for r in history_rows:
                new_date = to_year + r["date"][4:]
                if new_date in existing_target_dates:
                    skipped += 1
                    continue
                pests = json.loads(r["pests"]) if r["pests"] else []
                conn.execute(
                    """INSERT INTO spray_schedule
                       (schedule_date, actual_date, status, trigger_type, trigger_ref,
                        eval_box_id, rb_out_json, set_ids, pesticide_ids, operator,
                        weather, notes, field_id, created_at, updated_at)
                       VALUES (?, NULL, 'scheduled', 'cycle', ?, NULL, NULL, '[]', '[]', NULL, NULL, ?, ?, ?, ?)""",
                    (
                        new_date,
                        r["date"],
                        "、".join(pests) if pests else None,
                        r["field_id"],
                        now,
                        now,
                    ),
                )
                created += 1

        conn.commit()
        conn.close()
        self._send_json(200, {
            "status": "OK",
            "source": source,
            "fromYear": from_year,
            "toYear": to_year,
            "created": created,
            "skipped": skipped,
        })
        return


    # ─── 境界言語の形式知（rbp_*）CRUD（🧩 知識メンテ）─────────────
    # /api/rbp/<table>            : GET 一覧 / POST 作成
    # /api/rbp/<table>/<id>       : GET 1件 / PUT 更新 / DELETE 削除
    # <table> は _RBP_TABLES のキー。JSON 文字列列は文字列のまま保存。

    def _rbp_parse(self):
        """/api/rbp/... を (table, id or None) に分解。不正なら None。"""
        parts = [p for p in self.path.split("/") if p]
        # parts = ["api", "rbp", <table>, <id>?]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "rbp":
            return None
        table = parts[2]
        if table not in _RBP_TABLES:
            return None
        row_id = parts[3] if len(parts) >= 4 else None
        return table, row_id

    def _rbp_read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _rbp_values(self, table, body, exclude_pk=True):
        """body からテーブルのカラム値を抽出（宣言カラムのみ・順序保持）。"""
        cols = _RBP_TABLES[table]["columns"]
        vals = []
        for c in cols:
            if exclude_pk and c == _RBP_TABLES[table]["pk"]:
                continue
            v = body.get(c)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        return cols, vals

    # 普遍構造 T = {認知,評価,決定,投射} + 作動 の5段 → 各段の rbp_* テーブル。
    # タスクの接地 = 5段すべてに少なくとも1行あること（DB からライブ判定）。
    _STAGE_TABLES = [
        ("perception", "認知", "rbp_perception_axis"),
        ("evaluation", "評価", "rbp_eval_box"),
        ("decision",   "決定", "rbp_spec_condition"),   # 決定=仕様条件（ブリッジ/ゲートも併記）
        ("projection", "投射", "rbp_projection"),
        ("actuation",  "作動", "rbp_actuation"),
    ]

    def _task_grounding(self, conn, task_id):
        """タスクの5段（認知/評価/決定/投射/作動）の行数を DB からライブ判定する。

        決定段は rbp_spec_condition / rbp_bridge / rbp_spec_gate のいずれかに
        行があれば充足（仕様決定は条件・ブリッジ・ゲートの3面）。
        """
        stages = []
        grounded = 0
        for key, name, table in self._STAGE_TABLES:
            if key == "decision":
                n = 0
                for t in ("rbp_spec_condition", "rbp_bridge", "rbp_spec_gate"):
                    n += conn.execute(
                        f"SELECT COUNT(*) FROM {t} WHERE task_id=?", (task_id,)).fetchone()[0]
            else:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (task_id,)).fetchone()[0]
            present = n > 0
            grounded += 1 if present else 0
            stages.append({"key": key, "name": name, "table": table,
                           "count": n, "present": present})
        return {"grounded": grounded == len(self._STAGE_TABLES),
                "grounded_count": grounded, "total": len(self._STAGE_TABLES),
                "stages": stages}

    def _handle_rbp_tasks_get(self):
        """/api/rbp/tasks — タスク（業務）一覧＋各タスクの接地状態（5段・ライブ）。"""
        conn = get_db()
        try:
            tasks = []
            for t in conn.execute(
                    "SELECT * FROM rbp_task ORDER BY created_at, task_id").fetchall():
                d = dict(t)
                d["grounding"] = self._task_grounding(conn, d["task_id"])
                tasks.append(d)
            self._send_json(200, {"tasks": tasks,
                                  "stages": [{"key": k, "name": n, "table": tb}
                                             for k, n, tb in self._STAGE_TABLES]})
        finally:
            conn.close()

    def _handle_rbp_task_knowledge(self):
        """/api/rbp/tasks/<task_id>/knowledge — タスクの5段ナレッジ（行）を返す。

        ペトリネットの T ノードクリックで「認知→評価→決定→投射＋作動」の5段モデルを
        表示し、各段の「ナレッジ」クリックでこの段の実際の知識行（rbp_*）を見せる
        （§12.7 ナレッジ設計方法論・作動アンカー）。決定段は仕様条件・ブリッジ・
        ゲートの3面を併記（_task_grounding と同一の充足条件）。
        """
        # /api/rbp/tasks/<task_id>/knowledge → task_id を抽出
        parts = [p for p in self.path.split("/") if p]
        # parts = ["api", "rbp", "tasks", <task_id>, "knowledge"]
        if len(parts) < 5 or parts[2] != "tasks" or parts[4] != "knowledge":
            self._send_json(404, {"error": "not found"})
            return
        task_id = parts[3]
        conn = get_db()
        try:
            trow = conn.execute(
                "SELECT * FROM rbp_task WHERE task_id=?", (task_id,)).fetchone()
            if not trow:
                self._send_json(404, {"error": f"task {task_id} not found"})
                return
            stages = []
            for key, name, table in self._STAGE_TABLES:
                if key == "decision":
                    rows = []
                    # rbp_spec_gate の PK は domain_id（id 列なし）→ 表ごとに順序カラムを分ける。
                    for dt, ordcol in (("rbp_spec_condition", "id"), ("rbp_bridge", "id"),
                                       ("rbp_spec_gate", "domain_id")):
                        for r in conn.execute(
                                f"SELECT * FROM {dt} WHERE task_id=? ORDER BY {ordcol}", (task_id,)).fetchall():
                            rows.append({"table": dt, **dict(r)})
                else:
                    rows = [dict(r) for r in conn.execute(
                        f"SELECT * FROM {table} WHERE task_id=? ORDER BY id", (task_id,)).fetchall()]
                stages.append({"key": key, "name": name, "table": table,
                               "count": len(rows), "present": len(rows) > 0, "rows": rows})
            self._send_json(200, {
                "task_id": task_id,
                "task_name": trow["name"],
                "task_description": trow["description"],
                "grounding": self._task_grounding(conn, task_id),
                "stages": stages,
            })
        finally:
            conn.close()

    # ─── 投射：手順書テンプレートの合成導出（§12.7）─────────────
    # rbp_projection のテンプレート（$[本舗] / $[白黒マルチ]）に、仕様決定で抽出した
    # 変数（圃場名・マルチ資材名）を合成して手順書を導出する。人間が登録したのは
    # テンプレートと緒言DB（beds/mulch_materials）への指針、合成はシステムが自動で
    # 行う（「人間のやること=緒言登録、システムのやること=合成・導出」）。
    def _mulch_projection_synthesis(self, conn, field_id):
        """マルチ敷設の手順書を合成導出する。

        $[本舗]   → fields.name（圃場名）
        $[白黒マルチ] → beds.mulch_type → mulch_materials.name（選定された資材名）
        テンプレートは rbp_projection（task_id='mulch'）から読む。
        """
        import re as _re
        tpl_row = conn.execute(
            "SELECT template FROM rbp_projection WHERE task_id='mulch' "
            "ORDER BY id LIMIT 1").fetchone()
        if not tpl_row or not tpl_row["template"]:
            return None
        template = tpl_row["template"]

        # 圃場名（$[本舗] の変数）。
        field_name = None
        if field_id:
            frow = conn.execute(
                "SELECT name FROM fields WHERE id=?", (int(field_id),)).fetchone()
            if frow:
                field_name = frow["name"]
        else:
            frow = conn.execute(
                "SELECT name FROM fields WHERE type='main' ORDER BY id LIMIT 1").fetchone()
            if frow:
                field_name = frow["name"]

        # 選定されたマルチ資材名（$[白黒マルチ] の変数）。
        # beds.mulch_type（必要なマルチ種別）→ mulch_materials.type で候補選択。
        material_name = None
        mulch_type = None
        if field_id:
            brow = conn.execute(
                "SELECT mulch_type FROM beds WHERE field_id=? AND mulch_type IS NOT NULL "
                "ORDER BY seq, id LIMIT 1", (int(field_id),)).fetchone()
            if brow:
                mulch_type = brow["mulch_type"]
        if mulch_type:
            mrow = conn.execute(
                "SELECT name FROM mulch_materials WHERE type=? ORDER BY id LIMIT 1",
                (mulch_type,)).fetchone()
            if mrow:
                material_name = mrow["name"]
        if material_name is None:
            mrow = conn.execute(
                "SELECT name FROM mulch_materials ORDER BY id LIMIT 1").fetchone()
            if mrow:
                material_name = mrow["name"]

        # 合成: $[本舗]→圃場名 / $[白黒マルチ]→資材名（未解決は $[...] のまま残す）。
        def _sub(m):
            var = m.group(1)
            if var == "本舗" and field_name:
                return field_name
            if var in ("白黒マルチ", "マルチ") and material_name:
                return material_name
            return m.group(0)
        composed = _re.sub(r"\$\[([^\]]+)\]", _sub, template)

        return {
            "template": template,
            "composed": composed,
            "variables": {
                "本舗": field_name,
                "白黒マルチ": material_name,
            },
            "field_id": field_id,
            "field_name": field_name,
            "mulch_type": mulch_type,
            "material_name": material_name,
        }

    def _handle_mulch_projection(self):
        """/api/mulch/projection — マルチ敷設手順書の合成導出（投射）。"""
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        field_id = params.get("field_id")
        conn = get_db()
        try:
            result = self._mulch_projection_synthesis(conn, int(field_id) if field_id else None)
            if result is None:
                self._send_json(404, {"error": "mulch projection template not found"})
                return
            self._send_json(200, result)
        finally:
            conn.close()

    def _handle_mulch_prescribe(self):
        """/api/mulch/prescribe — マルチ敷設のライブ実行（5段 RBP プログラム）。

        薬剤選定（POST /api/prescribe）と同型: マルチ選定要求トークン（field_id から
        導出 or 直接指定）を認知→評価（グループ選択+ミラーID）→決定（仕様抽出）→
        投射→（任意で）作動（Slack）。判断ルールは DB（rbp_* task_id='mulch'）から
        読み、コードに持たない（[[jobnet-job-navigator]]）。
        body: {field_id?: int, token?: {object,context,beds,...}, send_slack?: bool}
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return
        field_id = body.get("field_id")
        token = body.get("token")
        if not token and (not isinstance(field_id, int) or field_id <= 0):
            self._send_json(400, {"error": "field_id (positive int) or token object required"})
            return
        send_slack = bool(body.get("send_slack", False))

        import mulch
        conn = get_db()
        try:
            result = mulch.prescribe(conn, field_id=field_id, token=token,
                                     send_slack=send_slack)
            self._send_json(200, result)
        except Exception as e:  # noqa: BLE001 — 認知軸未登録等
            self._send_json(500, {"error": f"mulch prescribe error: {e}"})
        finally:
            conn.close()

    def _handle_rbp_get(self):
        parsed = self._rbp_parse()
        if not parsed:
            self._send_json(404, {"error": "not found"})
            return
        table, row_id = parsed
        conn = get_db()
        try:
            if row_id is None:
                rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() \
                    if _RBP_TABLES[table]["auto"] \
                    else conn.execute(f"SELECT * FROM {table}").fetchall()
                self._send_json(200, {table: [dict(r) for r in rows]})
            else:
                pk = _RBP_TABLES[table]["pk"]
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE {pk}=?", (row_id,)).fetchone()
                if row:
                    self._send_json(200, dict(row))
                else:
                    self._send_json(404, {"error": f"{table} {row_id} not found"})
        finally:
            conn.close()

    def _handle_rbp_post(self):
        parsed = self._rbp_parse()
        if not parsed or parsed[1] is not None:
            self._send_json(404, {"error": "not found"})
            return
        table = parsed[0]
        try:
            body = self._rbp_read_body()
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return
        cols, vals = self._rbp_values(table, body, exclude_pk=False)
        conn = get_db()
        try:
            placeholders = ", ".join("?" for _ in cols)
            col_list = ", ".join(cols)
            cur = conn.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", vals)
            conn.commit()
            self._send_json(201, {"status": "created", "id": cur.lastrowid})
        except sqlite3.Error as e:
            self._send_json(400, {"error": f"insert failed: {e}"})
        finally:
            conn.close()

    def _handle_rbp_put(self):
        parsed = self._rbp_parse()
        if not parsed or parsed[1] is None:
            self._send_json(404, {"error": "not found"})
            return
        table, row_id = parsed
        try:
            body = self._rbp_read_body()
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return
        pk = _RBP_TABLES[table]["pk"]
        conn = get_db()
        try:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {pk}=?", (row_id,)).fetchone()
            if row is None:
                self._send_json(404, {"error": f"{table} {row_id} not found"})
                return
            merged = dict(row)
            merged.update(body)
            merged[pk] = row_id
            cols, vals = self._rbp_values(table, merged, exclude_pk=True)
            set_clause = ", ".join(f"{c}=?" for c in cols)
            conn.execute(
                f"UPDATE {table} SET {set_clause} WHERE {pk}=?",
                vals + [row_id])
            conn.commit()
            self._send_json(200, {"status": "updated", "id": row_id})
        except sqlite3.Error as e:
            self._send_json(400, {"error": f"update failed: {e}"})
        finally:
            conn.close()

    def _handle_rbp_delete(self):
        parsed = self._rbp_parse()
        if not parsed or parsed[1] is None:
            self._send_json(404, {"error": "not found"})
            return
        table, row_id = parsed
        pk = _RBP_TABLES[table]["pk"]
        conn = get_db()
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE {pk}=?", (row_id,))
            conn.commit()
        except sqlite3.Error as e:
            self._send_json(400, {"error": f"delete failed: {e}"})
            return
        finally:
            conn.close()
        if cur.rowcount == 0:
            self._send_json(404, {"error": f"{table} {row_id} not found"})
            return
        self._send_json(200, {"status": "deleted", "id": row_id})


def main():
    # 発火経路のログ（認知 NG 等）を stderr へ出力（nohup → server.log）。
    if not logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s [stb] %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(_h)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    init_db()
    # Listen backlog increased to 128 (class attr) to prevent browser connect stalls
    server = ThreadingHTTPServer(("0.0.0.0", 9999), Handler)
    print(f"Serving on 0.0.0.0:9999 — DB: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
