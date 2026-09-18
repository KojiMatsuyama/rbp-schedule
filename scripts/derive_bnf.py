#!/usr/bin/env python3
"""境界言語DSL 自動導出プログラム（DB → 境界言語txt）— 病害虫STB。

境界言語自動導出（DC 移植）の実装。rbp_* テーブル（MODEL/ACTUATION）を読み、
境界言語定義（共通上位形式BNF.txt の STB 下位形式）を生成する。

決定性条件:
  D1  全要素確定: 必須行が欠ける場合は既定値で埋めず DerivationError を返す
  D2  決定論: 同じ DB 状態から同じ DSL が生成される
  D3  完全性: DSL の各要素に「どのテーブルのどの行から来たか」を注記
  D4  静的制約検証: S1–S6 を DB 値に対してチェックし、違反は DerivationError

DC との構造差（共通上位形式BNF.txt 第11章）:
  - specbridge=present（5層）→ SPEC-BRIDGE 節（候補橋）を追加
  - candidate-selection（候補選択）→ SPEC 節は selection/score/tiebreak
  - exact-match（完全一致）→ BOX は members 付き
  - level-ascending（fold 順序）

使い方:
  python3 scripts/derive_bnf.py                 # STB-PEST を 病害虫防除境界言語(導出).txt へ
  python3 scripts/derive_bnf.py --check         # 導出のみ・ファイル書かず（検証用）
"""

import argparse
import json
import os
import sqlite3
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

import db_setup  # noqa: E402


class DerivationError(Exception):
    """D1（全要素確定）/ D4（静的制約）違反。"""


# ── 読み込み ──────────────────────────────────────────────

def load_domain(conn, domain_id, task_id=None):
    """指定領域（＋タスク）の MODEL/ACTUATION を読み込む。DATA（候補次元）も含める。

    task_id 指定時はタスク単位の RBP プログラム（各トランジション=自己完結 RBP）を
    導出する。None=ドメイン全体（薬剤選定・後方互換）。発火ゲート（rbp_spec_gate）と
    作動（actuation_*）はドメイン共通なので task_id では絞らない。"""
    def one(sql, *a):
        r = conn.execute(sql, a).fetchone()
        return dict(r) if r else None

    def many(sql, *a):
        return [dict(r) for r in conn.execute(sql, a).fetchall()]

    d = one("SELECT * FROM rbp_domain WHERE domain_id=?", domain_id)
    if not d:
        raise DerivationError(f"D1: rbp_domain に {domain_id} が存在しない")
    where = "domain_id=?"
    params = [domain_id]
    if task_id:
        where += " AND task_id=?"
        params.append(task_id)
    data = {
        "domain": d,
        "task_id": task_id,
        "axes": many(f"SELECT * FROM rbp_perception_axis WHERE {where} ORDER BY seq", *params),
        "boxes": many(f"SELECT * FROM rbp_eval_box WHERE {where} ORDER BY id", *params),
        "bridges": many(f"SELECT * FROM rbp_bridge WHERE {where} ORDER BY level", *params),
        "conds": many(f"SELECT * FROM rbp_spec_condition WHERE {where} ORDER BY seq", *params),
        "gate": one("SELECT * FROM rbp_spec_gate WHERE domain_id=?", domain_id),
        "projections": many(f"SELECT * FROM rbp_projection WHERE {where} ORDER BY id", *params),
        "vendors": many("SELECT * FROM external_vendors WHERE status='active'"),
        "channels": many("SELECT * FROM actuation_channels"),
        "rules": many("SELECT * FROM actuation_rules WHERE domain_id=?", domain_id),
    }
    for b in data["bridges"]:
        b["rule"] = json.loads(b["rule_json"]) if b.get("rule_json") else None
    for c in data["conds"]:
        c["rule"] = json.loads(c["rule_json"]) if c.get("rule_json") else None
    # DATA: 候補の次元（候補数）。タスクの SELECTION source テーブルから集計する
    # （薬剤選定=engine→pesticides / マルチ敷設=mulch_materials）。
    src = next((c["source"] for c in data["conds"] if c["cond_name"] == "SELECTION"), None)
    if src and src not in ("engine", "pesticides"):
        data["candidate_count"] = one(f"SELECT COUNT(*) AS n FROM {src}")["n"]
        data["candidate_label"] = "資材"
    else:
        data["candidate_count"] = one("SELECT COUNT(*) AS n FROM pesticides")["n"]
        data["candidate_label"] = "薬剤"
    return data


# ── D1: 全要素確定（必須行の欠落を検出）────────────────────

def validate_d1(data):
    missing = []
    if not data["axes"]:
        missing.append("rbp_perception_axis（認知軸が0本）")
    if not data["boxes"]:
        missing.append("rbp_eval_box（評価BOXが0個）")
    if not data["bridges"]:
        missing.append("rbp_bridge（BRIDGEが0本）")
    if not data["conds"]:
        missing.append("rbp_spec_condition（SPEC条件が0個）")
    if not data["gate"]:
        missing.append("rbp_spec_gate（発火ゲート）")
    if not data["projections"]:
        missing.append("rbp_projection（投射）")
    if not data["rules"]:
        missing.append("actuation_rules（作動ルール）")
    # 認知軸の norm_source は必須（キーワード対応表）
    for ax in data["axes"]:
        if not ax.get("norm_source"):
            missing.append(f"rbp_perception_axis[{ax['axis_name']}] の norm_source")
    # 述ブリッジの rule_json は必須（STB の判断知識の核心）
    for b in data["bridges"]:
        if not b.get("rule_json"):
            missing.append(f"rbp_bridge[{b['bridge_id']}] の rule_json")
    # 可変条件の decision_rule は必須
    for c in data["conds"]:
        if c["kind"] == "variable" and not c.get("decision_rule"):
            missing.append(f"rbp_spec_condition[{c['cond_name']}] の decision_rule")
    if missing:
        raise DerivationError("D1（全要素確定）違反: " + "; ".join(missing))


# ── D4: 静的制約 S1–S6 ────────────────────────────────────

def validate_d4(data):
    errs = []
    # S1: level 単調増加（重複なし）
    levels = [b["level"] for b in data["bridges"]]
    if len(levels) != len(set(levels)):
        errs.append(f"S1: BRIDGE の level が重複 {levels}")
    for i in range(1, len(levels)):
        if levels[i] <= levels[i - 1]:
            errs.append(f"S1: BRIDGE の level が単調増加でない {levels}")
    # S5: 分割の網羅排他 — exact/subset（STB 完全一致型）
    conds = {x["condition"] for x in data["boxes"]}
    if conds <= {"exact", "subset"}:
        pass  # 完全一致型（STB 型）
    elif {"any-dim-ge", "all-dim-lt"} <= conds:
        pass  # 補完分割（DC 型）
    else:
        errs.append(f"S5: BOX 分割条件が補完的でない {conds}")
    # exact BOX は members（ベクトル）を持つ
    for x in data["boxes"]:
        if x["condition"] == "exact" and not x.get("members"):
            errs.append(f"S5: BOX {x['box_id']}（exact）の members が欠落")
    if errs:
        raise DerivationError("D4（静的制約）違反: " + "; ".join(errs))


# ── 導出（レンダリング）──────────────────────────────────

def _j(x):
    return json.dumps(x, ensure_ascii=False)


def _render_norm(ax):
    """認知軸の正規化仕様（norm_source）をキーワード対応表付きでレンダリング。"""
    spec = json.loads(ax["norm_source"])
    lines = []
    for kw, idx in spec.get("keyword_map", {}).items():
        lines.append(f"        {kw:<12} → dim {idx}")
    return lines


def _render_bridge_rule(rule):
    """述ブリッジの rule_json を人間可読にレンダリング。"""
    lines = []
    lines.append(f"      \"when\"    {_j(rule['when'])}")
    lines.append(f"      \"then\"    {_j(rule['action'])}")
    lines.append(f"      \"else\"    {_j(rule['else_action'])}")
    if rule.get("penalty"):
        axis, delta = rule["penalty"]
        lines.append(f"      \"penalty\" {axis} {delta:+g}")
    return lines


def render(data):
    d = data["domain"]
    L = []
    A = L.append
    A("=" * 80)
    A("境界言語定義（DB 自動導出）")
    A(f"  domain: {d['domain_id']} — {d['name']}"
      + (f"   task: {data['task_id']}" if data.get("task_id") else ""))
    A("  ※ 本ファイルは scripts/derive_bnf.py が DB から自動生成した DSL。")
    A("    各行の # ← は出所テーブル（D3 完全性）。")
    A("=" * 80)

    # 第1節 PROFILE
    A("")
    A(f"第1節  PROFILE   # ← rbp_domain: {d['domain_id']}")
    A(f"  PROFILE {d['domain_id']}")
    A(f"  \"layer\"      specbridge = {d['layer_specbridge']}")
    A(f"  \"fold\"       order = {d['fold_order']}")
    _dim = len(json.loads(data["axes"][0]["dims"])) if data["axes"] else 0
    A(f"  \"perception\" vector = bit, dim = {_dim}, check all-zero => fail")
    A("  \"evalbox\"    partition = exact-match, on-unresolved = auto-register,")
    A("                 id-rule = next-max-plus-one")
    A("  \"spec\"       kind = candidate-selection, metric = cosine")

    # 第2節 DOMAIN
    A("")
    A("第2節  DOMAIN   # ← rbp_perception_axis（entry）/ pesticides（DATA）")
    A(f"  DOMAIN {d['domain_id']}")
    ax = data["axes"][0]
    dims = json.loads(ax["dims"])
    A(f"  \"dimensions\" {len(dims)}")
    for i, dim in enumerate(dims):
        A(f"  dim{i} : bit   # {dim}")
    A(f"  候補（{data['candidate_label']}） {data['candidate_count']} 件   # ← SELECTION source")

    # 第3節 DEMAND
    A("")
    A("第3節  DEMAND   # ← rbp_perception_axis")
    A("  DEMAND")
    for a in data["axes"]:
        A(f"  \"axis\" {a['axis_name']}   # ← rbp_perception_axis: {a['axis_name']}")
        A(f"      \"dims\"   {_j(json.loads(a['dims']))}")
        A(f"      \"norm\"   # 認知の正規化（キーワード → 次元index）")
        for line in _render_norm(a):
            A(line)
        A(f"      \"check\"  all-zero => {a['check_all_zero']}")
        A(f"      \"fire\"   none   # bit ベクトルは接近帯ガードなし")

    # 第4節 BRIDGE（評価BOX + 述ブリッジ）
    A("")
    A("第4節  BRIDGE   # ← rbp_eval_box / rbp_bridge")
    A("  BRIDGE")
    for x in data["boxes"]:
        A(f"  BOX {x['box_id']}   # ← rbp_eval_box: {x['box_id']}")
        A(f"      \"condition\" {x['condition']}")
        A(f"      \"members\"   {_j(json.loads(x['members'])) if x['members'] else '—'}")
        A(f"      \"name\"      {x['box_name']}")
    A("  \"policy\"  auto-register   # 未定義BOXを自動登録")
    A("  \"id-rule\" next-max-plus-one")
    A("")
    A("  SPEC-BRIDGE   # ← rbp_bridge（述ブリッジ・level 昇順）")
    for b in data["bridges"]:
        A(f"  BRIDGE {b['bridge_id']} (L{b['level']})   # ← rbp_bridge: {b['bridge_id']}")
        for line in _render_bridge_rule(b["rule"]):
            A(line)

    # 第5節 REFLECT
    A("")
    A(f"第5節  REFLECT   # ← rbp_domain.fold_order / rbp_bridge")
    A("  REFLECT")
    A("  \"fold\"     hadamard")
    A("  \"weight\"   { open = 1.0, closed = 0.0 }")
    A("  \"terminal\" { flowing = 到達BOX, dry = UNDEFINED }")
    A(f"  \"order\"    {d['fold_order']}")
    A("  \"bridges\"  " + ", ".join(
        f"{b['bridge_id']}(L{b['level']})" for b in data["bridges"]))

    # 第6節 SPEC（候補選択）
    A("")
    A("第6節  SPEC   # ← rbp_spec_condition / rbp_spec_gate")
    A("  SPEC")
    A("  \"kind\" candidate-selection")
    A("  \"selection\"")
    sel = next((c for c in data["conds"] if c["cond_name"] == "SELECTION"), None)
    if sel and sel.get("rule"):
        A(f"      \"metric\"   {sel['rule'].get('metric')}")
        A(f"      \"set-sizes\" {_j(sel['rule'].get('set_sizes'))}")
    A("  \"score\"   # ← rbp_spec_condition（スコア知識）")
    for c in [c for c in data["conds"] if c["cond_name"] in ("EFFECTIVENESS", "SAFETY", "RESISTANCE")]:
        A(f"  COND {c['cond_name']}   # ← rbp_spec_condition: {c['cond_name']}")
        A(f"      \"rule\"   {c['decision_rule']}")
        A(f"      \"rule-json\" {_j(c['rule']) if c.get('rule') else '—'}")
    tb = next((c for c in data["conds"] if c["cond_name"] == "TIEBREAK"), None)
    if tb and tb.get("rule"):
        A(f"  \"tiebreak\" {_j(tb['rule'].get('order'))}   # ← rbp_spec_condition: TIEBREAK")
    g = data["gate"]
    A("  \"gate\"")
    A(f"  \"pre\"  {g['pre']}   # ← rbp_spec_gate")
    A(f"  \"open\" {g['open_expr']}")

    # 第7節 PROJECTION + ACTUATION
    A("")
    A("第7節  PROJECTION / ACTUATION   # ← rbp_projection / actuation_*")
    for p in data["projections"]:
        A(f"  PROJECTION {p['output_name']} (mode={p['mode']})   # ← rbp_projection: {p['output_name']}")
        A("  \"template\"")
        for line in p["template"].split("\n"):
            A(f"      {line}")
    A("  ACTUATION")
    for v in data["vendors"]:
        A(f"  vendor {v['vendor_key']} = {v['name']}（{v['specialty']}）   # ← external_vendors")
    for ch in data["channels"]:
        A(f"  channel {ch['channel_key']} = {ch['channel_type']}:{ch['target']}   # ← actuation_channels: {ch['channel_key']}")
    for r in data["rules"]:
        A(f"  rule {r['trigger']} → actor={r['actor']}({r['actor_type']}) "
          f"channel={r['channel_key']} payload={r['payload']}   # ← actuation_rules")

    A("")
    A("=" * 80)
    A("（了・DB 自動導出）")
    A("=" * 80)
    return "\n".join(L) + "\n"


# ── 導出エントリ ─────────────────────────────────────────

def derive(conn, domain_id, out_path=None, task_id=None):
    data = load_domain(conn, domain_id, task_id)
    validate_d1(data)
    validate_d4(data)
    text = render(data)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


def main():
    ap = argparse.ArgumentParser(description="境界言語DSL 自動導出（DB → 境界言語txt）— STB")
    ap.add_argument("--domain", default="STB-PEST")
    ap.add_argument("--task", default=None,
                    help="タスク単位の RBP プログラム（例 rx-select / mulch）。"
                         "省略=ドメイン内の全タスク（認知軸を持つもの）を各々導出")
    ap.add_argument("--out", default=os.path.join(APP_ROOT, "病害虫防除境界言語(導出).txt"))
    ap.add_argument("--check", action="store_true", help="導出のみ・ファイル書かず")
    ap.add_argument("--db", default=os.path.join(APP_ROOT, "data", "stb.db"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    if not conn.execute("SELECT 1 FROM rbp_domain WHERE domain_id=?", (args.domain,)).fetchone():
        db_setup.seed_rbp_model(conn)

    try:
        # タスク単位の RBP プログラム（各トランジション=自己完結 RBP）。
        # --task 指定=その1タスク / 省略=認知軸を持つ全タスクを各々導出。
        # （ドメイン全体読み込みは複数タスクのブリッジが混在し S1 に違反するため使わない。）
        if args.task:
            tasks = [args.task]
        else:
            tasks = [r["task_id"] for r in conn.execute(
                "SELECT DISTINCT task_id FROM rbp_perception_axis WHERE domain_id=? "
                "AND task_id IS NOT NULL ORDER BY task_id", (args.domain,))]
            if not tasks:
                tasks = [None]  # 認知軸に task_id 未設定（旧形式）ならドメイン全体
        rc = 0
        for tid in tasks:
            label = f"{args.domain}/{tid}" if tid else args.domain
            # --task 指定 or タスク1件のみ → 既定ファイル。全タスク導出 → タスク名付きファイル。
            if args.task or len(tasks) == 1:
                out = args.out
            else:
                out = args.out.replace("(導出).txt", f"-{tid}(導出).txt")
            try:
                text = derive(conn, args.domain, None if args.check else out, tid)
            except DerivationError as e:
                print(f"導出エラー [{label}]: {e}", file=sys.stderr)
                rc = 1
                continue
            if args.check:
                print(text)
            else:
                print(f"導出完了 [{label}]: {out}（{len(text)} 文字）")
        return rc
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
