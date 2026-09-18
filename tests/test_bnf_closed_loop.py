"""クローズドループ検証: DB → 境界言語 → RBP 行列値 の自動導出を証明する（STB）。

境界言語自動導出（DC 移植）の実装。判断ルール（ブリッジ・スコア・BOX・認知軸・
ゲート）を **DB（rbp_* テーブル）から自動導出する汎用エンジン
（scripts/rbp_engine.py）**に置き換えることで、判断ルールを一切コードに持たず
DB のみから RBP 行列値を導出できることを証明する（「DB→実装」）。

    DB（rbp_*）→ RBPEngine（代数）→ RBP 行列値   ≟   現状実装（rbp-algebra-python）

検証項目（V4・STB 版）:
  V4a  認知: engine.check_ok / derive_target_vector == data_loader
  V4b  評価: engine.match_eval_box == main.match_eval_box（BOX 完全一致）
  V4c  反射: engine.run_line == core.run_line_through_bridges（述ブリッジ fold）
  V4d  決定: engine.build_prescription == main.build_prescription（候補選択）
  V4e  全パイプライン: engine.judge == api.prescribe（evalBox/status/best/trace）
  V4f  投射: rbp_projection のテンプレートが DB に存在（投射の形式知）
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
PY_ENGINE_DIR = os.path.join(APP_ROOT, "rbp-algebra-python")
if PY_ENGINE_DIR not in sys.path:
    sys.path.insert(0, PY_ENGINE_DIR)
if os.path.join(APP_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(APP_ROOT, "scripts"))

import db_setup  # noqa: E402
from rbp_engine import RBPEngine  # noqa: E402

import api as runtime_api  # noqa: E402
from main import match_eval_box as runtime_match_box  # noqa: E402
from data_loader import load_eval_boxes, load_pesticides  # noqa: E402
from core import run_line_through_bridges  # noqa: E402
from bridges import spec_bridges  # noqa: E402
from rbp_types import BridgeContext, EntryVector  # noqa: E402


# ============================================================================
# 一時DB（DATA + MODEL を seed）
# ============================================================================

def _seed_db():
    """一時DBに DATA（薬剤）+ MODEL（seed_rbp_model）を投入。
    (tmp, conn) を返す。呼び出し側は conn.close() する。"""
    tmp = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row
    conn.executescript(db_setup.CREATE_SQL)
    db_setup.seed_from_json(conn)   # 薬剤・BOX（data/*.json）
    db_setup.seed_rbp_model(conn)   # 境界言語の形式知（MODEL/ACTUATION）
    return tmp, conn


def _cleanup(tmp, conn):
    conn.close()
    if os.path.exists(tmp):
        os.unlink(tmp)


# 認知ベクトルのテストケース: 定義済みBOX / 複数病害 / 未定義 / 全0
VECTORS = [
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0],   # BOX-01 炭疽病のみ
    [0, 1, 0, 0, 0, 0, 0, 0, 0, 0],   # BOX-02 灰色かび病のみ
    [0, 0, 1, 0, 0, 0, 0, 0, 0, 0],   # BOX-03 うどんこ病のみ
    [0, 0, 0, 1, 0, 0, 0, 0, 0, 0],   # BOX-04 ハダニのみ
    [0, 1, 1, 0, 0, 1, 0, 0, 0, 0],   # EB-23（custom）灰色かび+うどんこ+オオタバコ
    [0, 1, 1, 1, 0, 1, 0, 0, 0, 0],   # 未定義（自動登録対象）
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],   # 全病害
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],   # 全0（認知NG）
]


# ============================================================================
# V4a  認知: DB駆動 engine == data_loader
# ============================================================================

def test_v4a_perception_target_vector_matches_loader():
    """engine.derive_target_vector（DB の keyword_map）== data_loader._derive_target_vector。"""
    tmp, conn = _seed_db()
    try:
        eng = RBPEngine(conn, "STB-PEST")
        loader_pests = load_pesticides()
        for p in loader_pests:
            names = json.loads(conn.execute(
                "SELECT targetNames FROM pesticides WHERE id=?", (p.pid,)).fetchone()[0])
            db_vec = eng.derive_target_vector(names)
            assert db_vec == list(p.target_vector.data), \
                f"target_vector 不一致 {p.pid}: db={db_vec} loader={list(p.target_vector.data)}"
    finally:
        _cleanup(tmp, conn)


# ============================================================================
# V4b  評価: DB駆動 engine.match_eval_box == main.match_eval_box
# ============================================================================

def test_v4b_eval_box_matches_runtime():
    """DB の rbp_eval_box（完全一致）== 実行時 match_eval_box（BOX 分類）。"""
    tmp, conn = _seed_db()
    try:
        eng = RBPEngine(conn, "STB-PEST")
        boxes = load_eval_boxes()
        for vec in VECTORS:
            ev = EntryVector(tuple(vec))
            impl_status, impl_detail = runtime_match_box(ev, boxes)
            db_status, db_detail = eng.match_eval_box(vec)
            assert db_status == impl_status, \
                f"BOX 分類不一致 vec={vec}: db={db_status} impl={impl_status}"
            if impl_status == "MATCH":
                assert db_detail == impl_detail, \
                    f"BOX id 不一致 vec={vec}: db={db_detail} impl={impl_detail}"
    finally:
        _cleanup(tmp, conn)


# ============================================================================
# V4c  反射: DB駆動 engine.run_line == core.run_line_through_bridges
# ============================================================================

def test_v4c_reflect_line_matches_runtime():
    """DB の rbp_bridge（述ブリッジ）から engine が導出した fold == core の fold。

    各薬剤・各ブリッジの重み（weight）と到達状態（flowing/blocked）を比較。"""
    tmp, conn = _seed_db()
    try:
        eng = RBPEngine(conn, "STB-PEST")
        pests = load_pesticides()
        ctx = {}  # clean slate（usage/rotation/interval 空）
        for vec in VECTORS[:4]:  # 定義済みBOXのみ（L1 で多数が流れる）
            ev = EntryVector(tuple(vec))
            for p in pests:
                tm = sum(min(tv, e) for tv, e in zip(p.target_vector.data, ev.data))
                rctx = BridgeContext(
                    pesticide=p, entry_vector=ev, target_match=tm,
                    usage_state={}, last_spray_date=None, last_pesticide_ids=[],
                    last_pesticides=[], interval_days=None, rotation_state={})
                impl = run_line_through_bridges(ev, spec_bridges, rctx)
                db = eng.run_line(vec, _to_engine_pesticide(p), ctx)
                # 重み列（L1〜L6）の一致
                impl_weights = [t.weight for t in impl.trace]
                db_weights = [t["weight"] for t in db["trace"]]
                assert db_weights == impl_weights, \
                    f"重み不一致 {p.pid} vec={vec}: db={db_weights} impl={impl_weights}"
                # 到達状態の一致
                impl_blocked = impl.state.__class__.__name__ == "Blocked"
                assert (db["state"] == "blocked") == impl_blocked, \
                    f"到達状態不一致 {p.pid} vec={vec}: db={db['state']} impl={impl_blocked}"
    finally:
        _cleanup(tmp, conn)


def _to_engine_pesticide(p):
    """runtime の Pesticide → engine の dict（run_line 入力の形）。"""
    return {
        "pid": p.pid, "name": p.name,
        "target_vector": list(p.target_vector.data),
        "max_applications": p.max_applications, "phi_days": p.phi_days,
        "toxicity_class": "HIGHLY_TOXIC" if p.toxicity_class.name == "HIGHLY_TOXIC" else "NON_TOXIC",
        "system_code": p.system_code, "system_name": p.system_name,
        "mixing_ban_targets": p.mixing_ban_targets,
    }


# ============================================================================
# V4d  決定: DB駆動 engine.build_prescription == main.build_prescription
# ============================================================================

def test_v4d_spec_prescription_matches_runtime():
    """DB の rbp_spec_condition（スコア知識）から engine が導出した処方 == 実行時。

    best の薬剤・スコア（mirrorId/totalScore）と除外個体を比較。"""
    tmp, conn = _seed_db()
    try:
        eng = RBPEngine(conn, "STB-PEST")
        pests = load_pesticides()
        for vec in VECTORS:
            ev = EntryVector(tuple(vec))
            impl = runtime_api.prescribe(vec)
            db = eng.judge(vec, eng.load_pesticides(), {})
            # status
            assert db["status"] == impl["status"], \
                f"status 不一致 vec={vec}: db={db['status']} impl={impl['status']}"
            # best の薬剤 id 列
            db_ids = [x["id"] for x in (db["best"]["pesticides"] if db["best"] else [])]
            impl_ids = [x["id"] for x in (impl["best"]["pesticides"] if impl["best"] else [])]
            assert db_ids == impl_ids, \
                f"best 薬剤不一致 vec={vec}: db={db_ids} impl={impl_ids}"
            # best のスコア
            if db["best"] and impl["best"]:
                assert db["best"]["mirrorId"] == pytest.approx(impl["best"]["mirrorId"]), \
                    f"mirrorId 不一致 vec={vec}"
                assert db["best"]["totalScore"] == pytest.approx(impl["best"]["totalScore"]), \
                    f"totalScore 不一致 vec={vec}: db={db['best']['totalScore']} impl={impl['best']['totalScore']}"
    finally:
        _cleanup(tmp, conn)


# ============================================================================
# V4e  全パイプライン: DB駆動 engine.judge == api.prescribe
# ============================================================================

def test_v4e_pipeline_full_matches():
    """認知→評価→反射→決定 の全パイプラインで DB駆動 == 実行時。"""
    tmp, conn = _seed_db()
    try:
        eng = RBPEngine(conn, "STB-PEST")
        for vec in VECTORS:
            impl = runtime_api.prescribe(vec)
            db = eng.judge(vec, eng.load_pesticides(), {})
            # 認知BOX
            assert db["evalBox"]["status"] == impl["evalBox"]["status"], \
                f"evalBox.status 不一致 vec={vec}"
            if impl["evalBox"]["status"] == "MATCH":
                assert db["evalBox"]["detail"] == impl["evalBox"]["detail"], \
                    f"evalBox.detail 不一致 vec={vec}"
            # 状態
            assert db["status"] == impl["status"], \
                f"status 不一致 vec={vec}: db={db['status']} impl={impl['status']}"
            # 代替案数
            assert len(db["alternatives"]) == len(impl["alternatives"]), \
                f"代替案数不一致 vec={vec}: db={len(db['alternatives'])} impl={len(impl['alternatives'])}"
            # 除外個体（pid 集合）
            db_ex = {e["pid"] for e in db["excludedIndividual"]}
            impl_ex = {e["pesticidePid"] for e in impl["excludedIndividual"]}
            assert db_ex == impl_ex, \
                f"除外個体不一致 vec={vec}: db={db_ex} impl={impl_ex}"
    finally:
        _cleanup(tmp, conn)


# ============================================================================
# V4f  投射: rbp_projection のテンプレートが DB に存在（投射の形式知）
# ============================================================================

def test_v4f_projection_templates_in_db():
    """投射の形式知（diagnostic / actuation-token / cron-text）が DB に格納されている。"""
    tmp, conn = _seed_db()
    try:
        eng = RBPEngine(conn, "STB-PEST")
        names = {p["output_name"] for p in eng.projections}
        for expected in ("diagnostic", "actuation-token", "cron-text"):
            assert expected in names, f"投射テンプレート欠落: {expected}"
        # テンプレートは非空
        for p in eng.projections:
            assert p["template"], f"投射テンプレートが空: {p['output_name']}"
    finally:
        _cleanup(tmp, conn)


# ============================================================================
# 独立実行: クローズドループ検証マトリクスを表示
# ============================================================================

def _report():
    tmp, conn = _seed_db()
    eng = RBPEngine(conn, "STB-PEST")
    print("\n=== クローズドループ: DB → 境界言語 → RBP 行列値 検証マトリクス（STB）===")
    print(f"{'ベクトル':<28}{'DB駆動エンジン':<30}{'実行時実装':<30}一致")
    print("-" * 100)
    all_ok = True
    for vec in VECTORS:
        impl = runtime_api.prescribe(vec)
        db = eng.judge(vec, eng.load_pesticides(), {})
        db_best = ",".join(x["id"] for x in (db["best"]["pesticides"] if db["best"] else []))
        impl_best = ",".join(x["id"] for x in (impl["best"]["pesticides"] if impl["best"] else []))
        ok = (db["status"] == impl["status"] and db_best == impl_best
              and db["evalBox"]["status"] == impl["evalBox"]["status"])
        all_ok = all_ok and ok
        print(f"{str(vec):<28}{db['status']+'/'+db_best:<30}{impl['status']+'/'+impl_best:<30}"
              f"{'✅' if ok else '❌'}")
    print("-" * 100)
    print(f"結果: {'✅ DB→境界言語→RBP行列値 の自動導出が成立（DB駆動 == 実行時実装）' if all_ok else '❌ 不一致あり'}")
    _cleanup(tmp, conn)
    return all_ok


if __name__ == "__main__":
    sys.exit(0 if _report() else 1)
