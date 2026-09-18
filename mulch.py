#!/usr/bin/env python3
"""
mulch.py — マルチ選定（task_id='mulch'）のライブ実行経路

「どんな単純でも構造は統一」（ユーザー方針）: 薬剤選定（rx-select）と同型の
5段 RBP プログラムを、マルチ選定もライブ実行する。各トランジション（タスク）
が自己完結した RBP プログラム（要求評価RBP行列→ミラーID→仕様決定RBP行列）
を持つ（[[jobnet-job-navigator]] / ts/TS設計.md §12.7）。

    認知(perception)  マルチ選定要求トークン（主体/定植/ベッド延長/幅）を認知
    評価(evaluation)  要求 → (context × type) の**グループ**選択 + ミラーID
    決定(decision)    仕様抽出（資材・列数・幅・延べ長さ）+ RBP エンジン（ミラーID/trace）
    投射(projection)  rbp_projection テンプレート → 敷設手順書（合成導出）
    作動(actuation)   sos.slack.send_message（敷設通知・T の正体）

**要求トークン駆動**（オブジェクト接地 §4.1・事業主体 §5.2）: 認知はベッド台帳
（beds）を直接読むのではなく、事業主体（三果倉）のスケジュール起動イベントで
発生する**マルチ選定要求トークン**（payload = 主体 / 定植 / 圃場(文脈) / ベッド延長 /
幅）を認知する。トークンが「誰の・どんな要求か」を自己完結して持つ。

判断ルールはすべて DB（rbp_* task_id='mulch'）から読み、コードに持たない。
"""

import json
import os
import re
import sqlite3

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in __import__("sys").path:
    __import__("sys").path.insert(0, APP_ROOT)

DOMAIN_ID = "STB-PEST"
TASK_ID = "mulch"


# =====================================================================
# 認知 (perception): マルチ選定要求トークン → 要求
# =====================================================================

def _perception_axis(conn):
    """マルチ選定の認知軸（rbp_perception_axis task_id='mulch'）を読み、
    dims と keyword_map（norm_source）を返す。"""
    row = conn.execute(
        "SELECT dims, norm_source FROM rbp_perception_axis "
        "WHERE domain_id=? AND task_id=? ORDER BY seq LIMIT 1",
        (DOMAIN_ID, TASK_ID)).fetchone()
    if not row:
        raise ValueError("認知軸（rbp_perception_axis task_id='mulch'）が未登録")
    dims = json.loads(row["dims"])
    norm = json.loads(row["norm_source"]) if row["norm_source"] else {}
    return dims, norm.get("keyword_map", {})


def _material_width(conn, mulch_type):
    """要求 type の資材幅（mulch_materials.width_cm）。無ければ None。"""
    row = conn.execute(
        "SELECT width_cm FROM mulch_materials WHERE type=? ORDER BY id LIMIT 1",
        (mulch_type,)).fetchone()
    return row["width_cm"] if row else None


# 事業主体（三果倉）＝ 要求の発生源・膜弧の起点（ts/真界_事業主体設計.md §2・§5）。
SUBJECT_ID = "mikakura"
SUBJECT_NAME = "三果倉"


def build_token(conn, field_id):
    """圃場（field_id）のベッド台帳からマルチ選定要求トークンの payload を導出する。

    オブジェクト接地 §4.1 + 事業主体（§5.2）: 要求トークンは**主体＋文脈**。
    object=主体（三果倉・膜弧の起点）、圃場（本圃）は文脈（field_id/field_name）
    に格降格。payload = 主体 / 定植 / ベッド延長 / 幅。

    戻り値:
        {object, subject_id, field_id, field_name, context,
         beds:[{name,seq,length_m,width_m,mulch_type}], total_length_m, width_cm}
    """
    field_row = conn.execute(
        "SELECT id, name FROM fields WHERE id=?", (int(field_id),)).fetchone()
    field_name = field_row["name"] if field_row else None
    beds = [dict(r) for r in conn.execute(
        "SELECT id, name, seq, length_m, width_m, mulch_type FROM beds WHERE field_id=? "
        "ORDER BY seq, id", (int(field_id),))]
    total_len = sum(b["length_m"] or 0.0 for b in beds)
    # 幅: 延べ長さ最大の type（primary）の資材幅（本圃=白黒→110cm）。
    primary_type = None
    best_len = -1.0
    for b in beds:
        if not b.get("mulch_type"):
            continue
        # type ごとに延べ長さを集計
        tlen = sum(x["length_m"] or 0.0 for x in beds if x.get("mulch_type") == b["mulch_type"])
        if tlen > best_len:
            best_len = tlen
            primary_type = b["mulch_type"]
    width_cm = _material_width(conn, primary_type) if primary_type else None
    return {
        "object": SUBJECT_NAME,
        "subject_id": SUBJECT_ID,
        "field_id": field_id,
        "field_name": field_name,
        "context": "定植",
        "beds": beds,
        "total_length_m": total_len,
        "width_cm": width_cm,
    }


def perceive_from_token(conn, token):
    """認知: マルチ選定要求トークンを認知する。

    トークンの beds を mulch_type でグルーピングし、各グループの要求を構築する
    （本圃 なら 白黒グループ + 黒グループ）。各要求は (context × type) の
    ベクトル化（one-hot）＋ オブジェクト属性（列数・延べ長さ・幅）。

    戻り値:
        {token, requirements:[{context,type,vector,columns,total_length_m,width_cm,beds}],
         field_id, field_name}
    """
    dims, keyword_map = _perception_axis(conn)
    groups = {}
    for bed in token.get("beds", []):
        mt = bed.get("mulch_type")
        if not mt:
            continue
        g = groups.setdefault(mt, {"type": mt, "beds": [], "total_length_m": 0.0})
        g["beds"].append(bed)
        g["total_length_m"] += bed.get("length_m") or 0.0
    requirements = []
    for mt, g in groups.items():
        vector = [0] * len(dims)
        for kw, idx in keyword_map.items():
            if mt == kw:
                vector[idx] = 1
        # 幅: 要求 type の資材幅（仕様決定で抽出する値）。
        width_cm = _material_width(conn, mt)
        requirements.append({
            "context": token.get("context", "定植"),
            "type": mt,
            "vector": vector,
            "columns": len(g["beds"]),
            "total_length_m": g["total_length_m"],
            "width_cm": width_cm,
            "beds": g["beds"],
        })
    # 延べ長さの大きい順（primary=白黒・延べ400m）。
    requirements.sort(key=lambda r: -r["total_length_m"])
    return {
        "token": token,
        "requirements": requirements,
        "field_id": token.get("field_id"),
        # 圃場名は文脈（field_name）から。旧トークン（object=圃場名）は後方互換で object。
        "field_name": token.get("field_name") or token.get("object"),
    }


def perceive(conn, field_id):
    """認知（後方互換ラッパー）: 圃場のベッド台帳をトークン化して認知する。

    旧シグネチャ（field_id → union ベクトル）を維持しつつ、トークン駆動の
    perceive_from_token に委譲する。"""
    token = build_token(conn, field_id)
    demand = perceive_from_token(conn, token)
    # 後方互換: union ベクトル（全要求の和）を付与。
    union = [0] * len(demand["requirements"][0]["vector"]) if demand["requirements"] else []
    for req in demand["requirements"]:
        for i, v in enumerate(req["vector"]):
            union[i] = union[i] or v
    dims, keyword_map = _perception_axis(conn)
    demand["vector"] = union
    demand["dims"] = dims
    demand["keyword_map"] = keyword_map
    demand["beds"] = token["beds"]
    return demand


# =====================================================================
# 評価 (evaluation): グループ選択 + ミラーID
# =====================================================================

def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = (sum(x * x for x in a)) ** 0.5
    nb = (sum(y * y for y in b)) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def evaluate(conn, requirement):
    """要求評価: (context × type) のグループ BOX を選択し、ミラーID を付与する。

    rbp_eval_box（task_id='mulch'）の各 BOX=グループ（context × mulch_type）。
    要求ベクトルと完全一致（members==vector）するグループを選択し、その参照
    ベクトル（members）と要求ベクトルのコサイン類似度=**ミラーID** を付与する
    （ユーザー指定 2026-09-07: 「定植白黒マルチグループが選択される、ミラーID」）。
    context 一致を優先（無ければ context=NULL の汎用 BOX）。

    戻り値:
        {group_id, group_name, mirror_id, context, vector, status}
    """
    rows = conn.execute(
        "SELECT box_id, box_name, members, context FROM rbp_eval_box "
        "WHERE domain_id=? AND task_id=? ORDER BY id", (DOMAIN_ID, TASK_ID)).fetchall()
    vec = requirement["vector"]
    ctx = requirement.get("context")
    best = None
    for r in rows:
        members = json.loads(r["members"]) if r["members"] else []
        if members != vec:
            continue
        cand = {
            "group_id": r["box_id"],
            "group_name": r["box_name"],
            "mirror_id": _cosine(members, vec),
            "context": r["context"],
        }
        if best is None:
            best = cand
        elif r["context"] == ctx and best.get("context") != ctx:
            best = cand
    if best is None:
        return {"group_id": None, "group_name": None, "mirror_id": 0.0,
                "context": ctx, "vector": vec, "status": "UNDEFINED"}
    best["vector"] = vec
    best["status"] = "MATCH"
    return best


# =====================================================================
# 決定 (decision): 仕様抽出 + RBP エンジン
# =====================================================================

def decide_spec(conn, requirement, selection):
    """仕様決定: グループから仕様（資材・列数・幅・延べ長さ）を抽出する。

    資材は mulch_materials で type 一致（候補プール）、幅は資材の width_cm、
    列数・延べ長さは要求（ベッド台帳）から。ミラーID は要求評価から継承。

    戻り値:
        {material_name, material_id, columns, width_cm, total_length_m,
         mirror_id, group_id, group_name, context, type}
    """
    mt = requirement["type"]
    mat = conn.execute(
        "SELECT id, name, width_cm FROM mulch_materials WHERE type=? ORDER BY id LIMIT 1",
        (mt,)).fetchone()
    material_name = mat["name"] if mat else None
    material_id = mat["id"] if mat else None
    width_cm = (mat["width_cm"] if mat else None) or requirement.get("width_cm")
    return {
        "material_name": material_name,
        "material_id": material_id,
        "columns": requirement["columns"],
        "width_cm": width_cm,
        "total_length_m": requirement["total_length_m"],
        "mirror_id": selection.get("mirror_id"),
        "group_id": selection.get("group_id"),
        "group_name": selection.get("group_name"),
        "context": requirement.get("context"),
        "type": mt,
    }


def _engine_judge(conn, vector, field_id):
    """RBP エンジン（RBPEngine task_id='mulch'）でミラーID・資材選定・trace を導出。

    decision.py::_call_rbp_engine_db と同型（RBPEngine → load_candidates → judge）。
    在庫（stock）は mulch_inventory を field_id で絞ってブリッジが判定する。"""
    import sys
    scripts_dir = os.path.join(APP_ROOT, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from rbp_engine import RBPEngine
        engine = RBPEngine(conn, DOMAIN_ID, TASK_ID)
        ctx = {"field_id": field_id} if field_id is not None else {}
        candidates = engine.load_candidates(ctx)
        return engine.judge(vector, candidates, ctx)
    finally:
        if scripts_dir in sys.path:
            sys.path.remove(scripts_dir)


def decide(conn, vector, field_id):
    """決定（後方互換）: RBP エンジンで資材選定（ミラーID スコアリング）。"""
    return _engine_judge(conn, vector, field_id)


# =====================================================================
# 投射 (projection): rbp_projection テンプレート → 敷設手順書
# =====================================================================

def project(conn, field_id, material_name):
    """投射: rbp_projection（task_id='mulch'）の手順書テンプレートを合成導出。

    $[本舗]→圃場名 / $[白黒マルチ]→選定資材名（未解決は $[...] のまま）。"""
    tpl_row = conn.execute(
        "SELECT template FROM rbp_projection WHERE task_id=? ORDER BY id LIMIT 1",
        (TASK_ID,)).fetchone()
    if not tpl_row or not tpl_row["template"]:
        return None
    template = tpl_row["template"]

    field_name = None
    if field_id is not None:
        frow = conn.execute(
            "SELECT name FROM fields WHERE id=?", (int(field_id),)).fetchone()
        if frow:
            field_name = frow["name"]

    def _sub(m):
        var = m.group(1)
        if var == "本舗" and field_name:
            return field_name
        if var in ("白黒マルチ", "マルチ") and material_name:
            return material_name
        return m.group(0)
    composed = re.sub(r"\$\[([^\]]+)\]", _sub, template)
    return {
        "template": template,
        "composed": composed,
        "variables": {"本舗": field_name, "白黒マルチ": material_name},
        "field_id": field_id,
        "field_name": field_name,
        "material_name": material_name,
    }


# =====================================================================
# 作動 (actuation): sos.slack.send_message（敷設通知・T の正体）
# =====================================================================

def _render_actuation_message(result):
    """敷設通知メッセージ（Slack）を合成する。"""
    demand = result["demand"]
    spec = result.get("spec") or {}
    selection = result.get("selection") or {}
    proj = result.get("projection") or {}
    field_label = demand.get("field_name") or ("圃場" + str(demand.get("field_id")))
    lines = ["🌱 マルチ敷設通知 — " + field_label]
    if selection.get("group_name"):
        lines.append(
            "要求評価: {}（ミラーID={:.2f}）".format(
                selection["group_name"], selection.get("mirror_id") or 0.0))
    if spec:
        lines.append(
            "仕様決定: {} {}列・幅{}cm・延べ{}m".format(
                spec.get("material_name") or "—", spec.get("columns"),
                spec.get("width_cm"), spec.get("total_length_m")))
    if proj.get("composed"):
        lines.append("")
        lines.append("【敷設手順】")
        lines.append(proj["composed"])
    return "\n".join(lines)


def actuate(result, send_slack=False):
    """作動: 敷設通知を Slack 送信（T の正体）。send_slack=False ならドライラン。"""
    if not send_slack:
        result["actuation"] = {"sent": False, "reason": "send_slack=False（ドライラン）"}
        return result
    try:
        import sos.slack
        message = _render_actuation_message(result)
        res = sos.slack.send_message(message)
        result["actuation"] = {"sent": bool(res.get("success")), "result": res}
    except Exception as e:  # noqa: BLE001 — 作動失敗は記録して処方自体は返す
        result["actuation"] = {"sent": False, "error": str(e)}
    return result


# =====================================================================
# 入口: 認知→評価→決定→投射→作動 の全判定
# =====================================================================

def prescribe(conn, field_id=None, token=None, send_slack=False):
    """マルチ選定のライブ実行（5段 RBP プログラム・トークン駆動）。

    マルチ選定要求トークン（field_id から導出 or 直接指定）を認知し、
    (context × type) のグループで要求評価（ミラーID 付与）、仕様を抽出、
    手順書を投射、（任意で）Slack 作動する。

    戻り値:
        {demand, selection, spec, all_specs, decision, projection, actuation}
        - demand   : 認知（トークン + グループごとの要求）
        - selection: 要求評価（primary のグループ選択 + ミラーID）
        - spec     : 仕様決定（primary の仕様: 資材/列数/幅/延べ長さ）
        - all_specs: 全要求の {requirement, selection, spec}
        - decision : RBP エンジン（ミラーID/資材選定/trace）+ spec/selection
    """
    if conn is None:
        raise ValueError("conn が必要です（server.get_db() で取得）")

    # 認知: トークン（field_id から導出 or 直接指定）
    if token is None:
        if field_id is None:
            raise ValueError("field_id または token が必要です")
        token = build_token(conn, field_id)
    demand = perceive_from_token(conn, token)
    requirements = demand["requirements"]
    field_id = token.get("field_id")

    if not requirements:
        result = {
            "demand": demand, "selection": None, "spec": None, "all_specs": [],
            "decision": {"status": "NO_REQUIREMENT", "best": None},
            "projection": None,
        }
        actuate(result, send_slack=send_slack)
        return result

    # primary = 延べ長さ最大の要求（本圃=白黒・延べ400m）。
    primary = requirements[0]
    # 要求評価: primary のグループ選択 + ミラーID。
    selection = evaluate(conn, primary)
    # 仕様決定: primary の仕様抽出。
    spec = decide_spec(conn, primary, selection)
    # RBP エンジン（ミラーID・資材選定・trace の一貫性）。
    decision = _engine_judge(conn, primary["vector"], field_id)
    decision["selection"] = selection
    decision["spec"] = spec
    # 投射: 選定資材名で手順書合成。
    projection = project(conn, field_id, spec.get("material_name"))
    # 全要求の仕様（参考: 本圃=白黒グループ + 黒グループ）。
    all_specs = []
    for req in requirements:
        sel = evaluate(conn, req)
        all_specs.append({
            "requirement": req, "selection": sel, "spec": decide_spec(conn, req, sel),
        })

    result = {
        "demand": demand,
        "selection": selection,
        "spec": spec,
        "all_specs": all_specs,
        "decision": decision,
        "projection": projection,
    }
    actuate(result, send_slack=send_slack)
    return result


def connect(db_path=None):
    """独立接続（CLI / テスト用）。server 経路は get_db() を使う。"""
    conn = sqlite3.connect(db_path or os.path.join(APP_ROOT, "data", "stb.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="マルチ選定 RBP ライブ実行（認知→評価→決定→投射→作動）")
    ap.add_argument("--field", type=int, default=None, help="圃場 id（トークンを導出）")
    ap.add_argument("--token", default=None, help="マルチ選定要求トークン（JSON）")
    ap.add_argument("--send-slack", action="store_true", help="作動: Slack 送信（既定はドライラン）")
    ap.add_argument("--db", default=os.path.join(APP_ROOT, "data", "stb.db"))
    args = ap.parse_args()
    if not args.field and not args.token:
        ap.error("--field または --token が必要です")
    conn = connect(args.db)
    try:
        tok = json.loads(args.token) if args.token else None
        out = prescribe(conn, field_id=args.field, token=tok, send_slack=args.send_slack)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        conn.close()
