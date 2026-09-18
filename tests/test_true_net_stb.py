"""真界（TrueNet / TN）完成検証: STB が共有 HUMOS OS（humos_os）で稼働することを証明。

DC と STB が**同一の OS コア `humos_os`**（/app/humos_os）で稼働する「真界の完成」を
機械的に検証する（STB 側融合の最終確認）。

    真界 = 記号界 + 物理界
    記号界 = HUMOS（SSS + STN）+ IF + SOS

検証項目:
  TN1  5 テーブル（place/token/marking/firing/writing）が STB DB に存在
  TN2  共有 OS の 3 関数（fire/enabled/write_sos）+ verify_replay が DB で動作
  TN3  STN 発火順序（マーキング駆動・分岐・固定点停止）
  TN4  在庫リフレックス（間接的開き）: T_rx_exec が place_token で在庫トークンを置き、
       ループがライブマーキング再読で T_inventory を発火させる
  TN5  融合の証明: DB駆動 decision.decide(conn) == 実行時 api.prescribe（複数ベクトル）
  TN6  不変条件2: STN（jobnet / humos_os.stn / humos_os.humos）は IF・SOS を
       直接 import しない（HUMOS のみ両界に開く）
  TN7  境界言語図表（/viz）: 共有 rbp_viz が STB ドメインで自己完結HTMLを生成
       （DC の /viz と同型・STB は2次元境界图等が非適用注記）
  TN8  候補選択の図表化: 図F が candidate-selection（候補プール→最良セット選択）
       を表現する（薬剤が DB から選択されることを図に表現）

※ 全テストは**一時DB**（STB_HUMOS_DB 相当）で実行。本番 data/stb.db は触らない。
"""

import json
import os
import sqlite3
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
if os.path.join(APP_ROOT, "rbp-algebra-python") not in sys.path:
    sys.path.insert(0, os.path.join(APP_ROOT, "rbp-algebra-python"))
if os.path.join(APP_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(APP_ROOT, "scripts"))
# rbp_viz は DC/STB 共通の共有パッケージ（/app/rbp_viz）。上位ディレクトリを path に加える。
_APP_DIR = os.path.dirname(APP_ROOT)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import db_setup  # noqa: E402
import humos  # noqa: E402
import jobnet  # noqa: E402
import perception  # noqa: E402
import rbp_viz  # noqa: E402
from humos_os import humos as _h  # noqa: E402
from humos_os import stn  # noqa: E402

# 認知ベクトルのテストケース（test_bnf_closed_loop.py と同一）。
VECTORS = [
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0],   # BOX-01 炭疽病のみ
    [0, 1, 0, 0, 0, 0, 0, 0, 0, 0],   # BOX-02 灰色かび病のみ
    [0, 0, 1, 0, 0, 0, 0, 0, 0, 0],   # BOX-03 うどんこ病のみ
    [0, 0, 0, 1, 0, 0, 0, 0, 0, 0],   # BOX-04 ハダニのみ
    [0, 1, 1, 0, 0, 1, 0, 0, 0, 0],   # EB-23（custom）
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],   # 全病害
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],   # 全0（認知NG）
]


# ============================================================================
# 一時DB fixture（DATA + MODEL を seed、humos wrapper を一時DBに向ける）
# ============================================================================

@pytest.fixture
def stb_db(tmp_path):
    """一時DBに DATA（薬剤）+ MODEL（seed_rbp_model）を投入し、humos wrapper を
    その一時DBに向ける（本番 data/stb.db を触らない）。(tmp, conn) を yield。"""
    tmp = str(tmp_path / "stb_test.db")
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row
    conn.executescript(db_setup.CREATE_SQL)
    db_setup.seed_from_json(conn)   # 薬剤・BOX（data/*.json）
    db_setup.seed_rbp_model(conn)   # 境界言語の形式知（MODEL/ACTUATION）
    conn.close()

    # humos wrapper を一時DBに向ける（_conn() は module global _DB_PATH を参照）。
    old_path = humos._DB_PATH
    humos._DB_PATH = tmp
    humos._conn_local.conn = None   # キャッシュされた接続を破棄 → 一時DBへ再接続
    yield tmp
    humos._conn_local.conn = None
    humos._DB_PATH = old_path
    if os.path.exists(tmp):
        os.unlink(tmp)


def _set_front_tokens():
    """前段 2 トークン（field_type / pest_matrix）を投入（run() 入口の前提）。"""
    humos.set_token("field_type", "🌱 育苗 育苗圃（200m²）")
    humos.set_token("pest_matrix", "[1,0,0,0,0,0,0,0,0,0]")


def _make_state(message="炭疽病"):
    """agentic_chat.run と同一の ChatState（dict 形状）を構築する。"""
    return {
        "messages": [{"role": "user", "content": message}],
        "intent": None, "identified_diseases": [], "vector": [0] * 10,
        "eval_token": None, "eval_box_id": None, "eval_box_name": None,
        "eval_status": None, "selection_token": None, "prescription": [],
        "mirror_id": None, "effectiveness": None, "line_traces": [],
        "excluded_drugs": [], "excluded_combos": [], "projected_message": None,
        "inventory_token": None, "inventory_check": None, "inventory_message": None,
        "executed_projection": False, "executed_inventory": False,
        "sent_to": None, "error": None,
    }


# ============================================================================
# TN1  5 テーブルが STB DB に存在
# ============================================================================

def test_tn1_five_tables_exist(stb_db):
    """place/token/marking/firing/writing の 5 テーブルが一時DBに存在する。"""
    conn = humos.get_conn()
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("place", "token", "marking", "firing", "writing"):
        assert t in tables, f"テーブル欠落: {t}"
    # STB NET の place スキーマが反映されている（field_type / pest_matrix 等）
    keys = {r["token_key"] for r in conn.execute("SELECT token_key FROM place")}
    for k in ("field_type", "pest_matrix", "inventory_token", "external_wait_token"):
        assert k in keys, f"place の token_key 欠落: {k}"


# ============================================================================
# TN2  共有 OS の 3 関数 + verify_replay が DB で動作
# ============================================================================

def test_tn2_shared_os_functions(stb_db):
    """fire / enabled / write_sos / verify_replay が DB 永続で動作する。"""
    conn = humos.get_conn()
    net = "mikakura"
    keep = humos.get_required_keys()
    humos.reset()

    # fire（post をマーキングに反映・発火ログ記録）
    _h.fire(conn, net, "T_decision", {"selection_token": "tok"},
            {"prescription": [{"id": "P03"}]}, hard_consume=False)
    marking = _h.get_marking_payloads(conn, net)
    assert marking.get("prescription") == [{"id": "P03"}], "fire が post を反映していない"

    # enabled（ライブマーキングで判定）
    assert _h.enabled(conn, net, ["prescription"]) is True, "enabled 判定が誤り"
    assert _h.enabled(conn, net, ["inventory_token"]) is False, "未生成トークンで enabled"

    # write_sos（マーキングを変えず writing のみ記録）
    _h.write_sos(conn, net, "slack_send", {"success": True})
    sos = [e for e in _h.get_writing_log(conn, net) if e["operator_kind"] == "sos"]
    assert sos and sos[-1]["action"] == "slack_send", "write_sos が記録されていない"
    assert _h.get_marking_payloads(conn, net).get("prescription") == [{"id": "P03"}], \
        "write_sos がマーキングを変えた（不変）"

    # verify_replay（不変条件5: マーキング ≡ M0 からのリプレイ）
    assert _h.verify_replay(conn, net, hard_consume=False, keep_keys=keep) is True, \
        "リプレイ整合が崩れている"


# ============================================================================
# TN3  STN 発火順序（マーキング駆動・分岐・固定点停止）
# ============================================================================

def test_tn3_stn_firing_order(stb_db, monkeypatch):
    """マーキング駆動の発火ループが正しい順序で全トランジションを発火する。"""
    monkeypatch.setattr(perception, "perceive",
                        lambda msg: {"identified_diseases": ["炭疽病"],
                                     "vector": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]})
    import agentic_chat.nodes as nodes
    monkeypatch.setattr(nodes, "_send_to_slack", lambda msg: {"success": True})

    _set_front_tokens()
    result = jobnet.run_net(_make_state())

    tids = [e["tid"] for e in humos.get_firing_log()]
    expected = {"T_state", "T_perception", "T_evaluation", "T_decision",
                "T_projection", "T_rx_exec", "T_inventory", "T_inventory_exec"}
    assert set(tids) == expected, f"発火トランジション不一致: {tids}"
    # 依存順序: 決定 < 投射/作動 < 在庫 < 在庫実行
    assert tids.index("T_decision") < tids.index("T_projection")
    assert tids.index("T_decision") < tids.index("T_rx_exec")
    assert tids.index("T_rx_exec") < tids.index("T_inventory")
    assert tids.index("T_inventory") < tids.index("T_inventory_exec")
    # 分岐: 投射1 と 作動(Slack) が prescription から両方発火
    assert result.get("projected_message"), "投射1（レポート）が生成されていない"
    assert result.get("prescription"), "処方（prescription）が生成されていない"


# ============================================================================
# TN4  在庫リフレックス（間接的開き）
# ============================================================================

def test_tn4_inventory_reflex(stb_db, monkeypatch):
    """T_rx_exec が place_token で在庫トークンを置き、ループが T_inventory を発火させる。

    在庫トークンは T_rx_exec の handler 返り値ではなく、humos.place_token 経由で
    マーキングに置かれ、発火前後のマーキング差分で検出される（間接的開き・§4.3）。
    """
    monkeypatch.setattr(perception, "perceive",
                        lambda msg: {"identified_diseases": ["炭疽病"],
                                     "vector": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]})
    import agentic_chat.nodes as nodes
    monkeypatch.setattr(nodes, "_send_to_slack", lambda msg: {"success": True})

    _set_front_tokens()
    result = jobnet.run_net(_make_state())

    fl = {e["tid"]: e for e in humos.get_firing_log()}
    # T_rx_exec の post は external_wait_token のみ。在庫トークンはリフレックス差分。
    rx_gen = fl["T_rx_exec"]["generated"]
    assert "external_wait_token" in rx_gen, "T_rx_exec が外部待ちトークンを生成していない"
    assert "inventory_token" in rx_gen, \
        "在庫リフレックス（place_token 差分）が T_rx_exec の生成として記録されていない"
    # 在庫トークンが置かれたことで T_inventory が発火し、在庫結果が生成された
    assert "T_inventory" in fl, "在庫リフレックスで T_inventory が発火していない"
    assert result.get("inventory_message"), "在庫チェック結果が生成されていない"
    assert result.get("inventory_token"), "在庫トークンが state に反映されていない"


# ============================================================================
# TN5  融合の証明: DB駆動 decide(conn) == 実行時 api.prescribe
# ============================================================================

def test_tn5_fusion_db_driven_matches_runtime(stb_db):
    """真界ループの判断核（DB駆動 rbp_engine）が実行時実装（JSON）と一致する。

    融合の核心: 真界ループ（jobnet → decision_node → decision.decide(conn)）が
    **DB の rbp_* 知識DB**で処方計算し、従来実行時（api.prescribe = data/*.json）と
    同一の結果を出す。これにより「真界の基で薬剤選択が動作」が成立する。

    空ベクトル（全0）は decide の安全ガード（NO_TARGET_IDENTIFIED）でエンジン
    到達前に短絡される（雑談への処方防止）。判断核の融合はエンジンに到達する
    ベクトルで検証し、空ベクトルはガードの動作を別途検証する。
    """
    import decision
    import api as runtime_api
    conn = humos.get_conn()
    for vec in VECTORS:
        db_res = decision.decide(vec, conn=conn)
        if sum(vec) == 0:
            # 空ベクトルガード: エンジン到達前に NO_TARGET_IDENTIFIED で短絡
            assert db_res["status"] == "NO_TARGET_IDENTIFIED", \
                f"空ベクトルガードが効いていない vec={vec}: {db_res['status']}"
            assert db_res["prescription"] == [], "空ベクトルで処方が出た（ガード破綻）"
            continue
        json_res = runtime_api.prescribe(vec)
        # status 一致
        assert db_res["status"] == json_res["status"], \
            f"status 不一致 vec={vec}: db={db_res['status']} json={json_res['status']}"
        # best 薬剤 id 列一致
        db_ids = [p["id"] for p in (db_res.get("prescription") or [])]
        json_ids = [p["id"] for p in (json_res.get("best", {}).get("pesticides") or [])]
        assert db_ids == json_ids, \
            f"best 薬剤不一致 vec={vec}: db={db_ids} json={json_ids}"


# ============================================================================
# TN6  不変条件2: STN は IF・SOS を直接 import しない
# ============================================================================

def test_tn6_stn_does_not_import_if_sos():
    """STN コア（jobnet / humos_os.stn / humos_os.humos）は IF・SOS を直接 import しない。

    両界に開くのは HUMOS だけ（間接的開き）。STN（スケジューラ）が SOS（作動）や
    IF（接地）を直接 import すると境界が破れる。
    """
    import re
    targets = [
        os.path.join(APP_ROOT, "jobnet.py"),
        os.path.join(APP_ROOT, "..", "humos_os", "stn.py"),
        os.path.join(APP_ROOT, "..", "humos_os", "humos.py"),
    ]
    for path in targets:
        path = os.path.normpath(path)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # `import sos` / `from sos import` / `import if` / `from if import` を検出
        for bad in (r"^\s*import\s+sos\b", r"^\s*from\s+sos\b",
                    r"^\s*import\s+if\b", r"^\s*from\s+if\b"):
            assert not re.search(bad, src, re.MULTILINE), \
                f"STN コアが IF/SOS を直接 import: {os.path.basename(path)} ({bad})"


# ============================================================================
# TN7  境界言語図表（/viz）: rbp_viz が STB ドメインで自己完結HTMLを生成
# ============================================================================

def test_tn7_viz_boundary_language_figure(stb_db):
    """共有 rbp_viz が STB（10次元・完全一致型）の境界言語図表を自己完結HTMLで生成する。

    DC の /viz と同型の図表化を STB に融合。rbp_viz はドメイン無依で、STB では
    2次元境界図・URGENCY 決定木・要求軸期限図が「非適用」注記になり、決定表・
    フロー・パイプ構造・代替判断・暦が描画される（/viz が生DBから動的生成）。
    """
    conn = humos.get_conn()
    row = conn.execute("SELECT domain_id FROM rbp_domain LIMIT 1").fetchone()
    assert row, "rbp_domain が空（境界言語が未シード）"
    model = rbp_viz.load_domain(conn, row["domain_id"])
    # STB は10次元・完全一致型 → 図A（2次元境界図）は非適用
    from rbp_viz import model as M
    assert M.is_boundary_2d(model) is False, "STB は2次元境界図非適用のはず"
    html = rbp_viz.render_html(model, back_href="/")
    # 自己完結（外部 http/https リソース参照なし）
    import re
    assert not re.findall(r'(?:src|href)="(http[^"]+)"', html), "図表が自己完結でない"
    # タイトル・セクション
    assert "境界言語図表" in html
    for section in ("決定表", "フロー", "パイプ構造", "代替判断"):
        assert section in html, f"図表セクション欠落: {section}"


# ============================================================================
# TN8  境界言語図表: 候補選択（candidate-selection）が図に表現される
# ============================================================================

def test_tn8_viz_candidate_selection(stb_db):
    """図F（パイプ構造）が STB の候補選択（candidate-selection）を表現する。

    仕様決定は「薬剤が DB（候補プール）から選択される」ことを描く。
    <Spec-Profile>.kind=candidate-selection なので、候補プール（DB シリンダー）と
    「候補プールから最良セット選択」「余弦類似度（Mirror-ID）」が描画され、
    投射は「処方文 = 選択候補 × 要求属性[D]」になる（DC の条件判定表現とは異なる）。
    meta は「候補選択」を表示する。
    """
    conn = humos.get_conn()
    row = conn.execute("SELECT domain_id FROM rbp_domain LIMIT 1").fetchone()
    model = rbp_viz.load_domain(conn, row["domain_id"])
    from rbp_viz import model as M
    # <Spec-Profile>.kind = candidate-selection
    assert M.spec_kind(model) == "candidate-selection", \
        f"STB は candidate-selection のはず: {M.spec_kind(model)}"
    assert M.is_candidate_selection(model) is True
    pool = M.candidate_pool(model)
    assert pool and pool["table"] == "pesticides", f"候補プール指針が不正: {pool}"
    assert pool["count"] and pool["count"] > 0, "候補プールの実数が読めていない"

    from rbp_viz import figures as F
    html, note = F.pipe_structure.figure(model)
    assert note is None
    # 候補選択の表現（DC の条件判定表現とは異なる）
    assert "候補プールから最良セット選択" in html, "候補選択（最良セット選択）が描画されていない"
    assert "余弦類似度" in html, "選択基準（余弦類似度 / Mirror-ID）が描画されていない"
    assert "薬剤候補" in html, "候補プール（薬剤候補）が描画されていない"
    assert f'{pool["count"]} 個' in html, "候補プールの実数が描画されていない"
    assert "処方文 = 選択候補" in html, "投射が選択候補で処方文を作る表現になっていない"
    # DC の条件判定表現（可変部抽出）は候補選択では出ない
    assert "可変部（変数）の抽出" not in html, "候補選択で DC の条件判定表現が出た"
    # meta は「候補選択」を表示
    full = rbp_viz.render_html(model, back_href="/")
    assert "候補選択" in full, "meta に「候補選択」が表示されていない"


# ============================================================================
# 独立実行: 真界完成マトリクスを表示
# ============================================================================

def _report():
    print("\n=== 真界（TN）完成検証: STB が共有 HUMOS OS で稼働 ===")
    print("TN1 5テーブル / TN2 共有OS関数 / TN3 発火順序 / TN4 在庫リフレックス /")
    print("TN5 融合(DB駆動==実行時) / TN6 STNはIF・SOS非import / TN7 境界言語図表 /")
    print("TN8 候補選択の図表化（候補プール→最良セット選択）")
    print("（pytest で実行: python3 -m pytest tests/test_true_net_stb.py -v）")


if __name__ == "__main__":
    _report()
    sys.exit(0)
