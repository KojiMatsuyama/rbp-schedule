#!/usr/bin/env python3
"""投射モジュール（独立トランジション）— 算出結果を文章に写像する。

三層構造（状態空間データネット × ナビゲーターネット × 物理界）において、
この投射は「ナビゲーターネット → 物理界」の境界ブリッジ。RBP が算出した
結果（薬剤・スコア・ブリッジtrace・代替案・在庫・状態）を、人間が読む
文章（診断レポート or Slack 文）に**写像**するだけの純粋な整形処理であり、
判定ロジックは持ちません。

このファイルをトップレベルに置く理由:
  - チャット版（agentic_chat）と cron 版（scripts/rx_prescribe.py）の
    両方が import するから。
  - agentic_chat/projection.py に置くと agentic_chat/__init__.py
    (= LangGraph 全套) を巻き込むため、cron スクリプトはそれを避けたい。
  - agentic_chat / scripts からの双方は APP_ROOT を sys.path に入れて
    `import projection` するだけで通る。

2つの投射レンダラ:
  render_projection  — チャット診断レポート（詳細・ブリッジ履歴付き）
  build_slack_text   — cron 防除暦向け Slack 短文
"""

# =====================================================================
# チャット投射 — render_projection
# =====================================================================
# 元: agentic_chat/nodes.py projection_node（NODE ④/⑤）。
# 判定ノードが生成する ChatState 各フィールドを読み取り、
# 送信-ready な診断レポート文字列を返す。

# ブリッジ L1-L6 の日本語ラベル
_BRIDGE_LABELS = [
    "L1ターゲット", "L2散布回数", "L3PHI残留日",
    "L4系統ローテーション", "L5混用可否", "L6毒性区分",
]


def render_projection(state: dict) -> str:
    """
    投射 — 決定された薬剤名・スコア・trace をメッセージテンプレートに埋め込む。

    例（テンプレート）:
      【評価BOX名】
      今回の防除の薬剤は、ベルクート、ダコニール1011、です。

      【スコア内訳】
      ミラーID: 0.95
      有効性スコア: 45.2
      - 有効性: ミラーID=0.95, カバレッジ=75% (3/4)
      - 安全性: 20.0
      - 抵抗性: 異なる系統（FRAC1／IRAC21A）の組み合わせ：抵抗性管理上有効

      【ブリッジ通過履歴（全候補）】
      ベルクート: L1=PASS L2=PASS L3=PASS L4=PASS L5=PASS L6=PASS

      【代替案】
      2位: ダコニール1011 (スコア: 42.1)

      【除外された薬剤】
      - アブラムシ: 混用不可（SPEC-BRIDGE-TOXICITY）

    Args:
        state: agentic_chat.state.ChatState（dict）。読み取り専用。

    Returns:
        送信-ready な診断レポート文字列。
    """
    drugs = state.get("prescription", [])
    eval_box_name = state.get("eval_box_name")
    identified = state.get("identified_diseases", [])
    mirror_id = state.get("mirror_id")
    effectiveness = state.get("effectiveness")
    bridge_trace = state.get("bridge_trace")
    excluded_drugs = state.get("excluded_drugs", [])
    excluded_combos = state.get("excluded_combos", [])
    alternatives = state.get("alternatives", [])
    status = state.get("status", "")

    # lineTraces（全connected lineのブリッジ通過履歴）
    line_traces = state.get("line_traces", [])

    parts = []

    # ---- ヘッダー ----
    if status == "NO_TARGET_IDENTIFIED":
        # 認知されない（雑談等）場合は処方結果を返さない。
        # 本来は intent="chat" で agentic_chat.run がLLM応答に置換するため
        # ここに到達しないが、念のため処方テンプレートを返さない。
        parts.append("【対応内容なし】")
        parts.append("今回のメッセージから病害虫の発生が認知されませんでした。")
        return "\n".join(parts)

    if status == "NO_PESTICIDE_DEFINED":
        parts.append("【対応薬剤なし】")
        parts.append("選択された病害虫に対して、登録済みの薬剤が定義されていません。")
        return "\n".join(parts)

    if status == "ALL_BLOCKED_BY_CONSTRAINTS":
        parts.append("【全薬剤除外】")
        parts.append("対応する薬剤は存在しますが、すべての薬剤が何らかの制約により選択できません。")
        if excluded_drugs:
            parts.append("")
            parts.append("【除外された薬剤】")
            for d in excluded_drugs[:10]:
                parts.append(f"- {d}")
        if excluded_combos:
            parts.append("")
            parts.append("【除外された2剤セット】")
            for c in excluded_combos[:10]:
                parts.append(f"- {c}")
        return "\n".join(parts)

    if status == "ENGINE_ERROR":
        error_msg = state.get("error", "RBPエンジンエラー")
        parts.append(f"【エラー】{error_msg}")
        return "\n".join(parts)

    # 正常系
    if eval_box_name:
        parts.append(f"【{eval_box_name}】")
    elif identified:
        parts.append(f"【{'、'.join(identified)}】")

    # ---- 処方結果 ----
    if drugs:
        drug_names = "、".join(d["name"] for d in drugs)
        parts.append(f"今回の防除の薬剤は、{drug_names}、です。")
    else:
        parts.append("（薬剤選定できませんでした）")

    # ---- スコア内訳 ----
    if mirror_id is not None:
        parts.append("")
        parts.append("【スコア内訳】")
        parts.append(f"ミラーID: {mirror_id:.2f}")
        if effectiveness is not None:
            parts.append(f"有効性スコア: {effectiveness:.1f}")

        # 個別薬剤スコア
        for d in drugs:
            score = d.get("score", d.get("mirrorId", 0))
            mr = d.get("mirrorId", 0)
            cr = d.get("coverageRatio", 0)
            line = f"- {d['name']}:"
            if mr:
                line += f" ミラーID={mr:.2f}"
            if cr:
                line += f" カバレッジ={cr:.0%}"
            parts.append(line)

        # breakdownがあれば詳細を表示
        if drugs and "breakdown" in drugs[0]:
            bd = drugs[0].get("breakdown", {})
            eff_bd = bd.get("effectiveness", {})
            sat_bd = bd.get("safety", {})
            res_bd = bd.get("resistance", {})

            if eff_bd:
                mi = eff_bd.get("mirrorId", 0)
                cov = eff_bd.get("coverageRatio", 0)
                mc = eff_bd.get("matchCount", 0)
                ts = eff_bd.get("targetSum", 0)
                parts.append(f"  有効性: ミラーID={mi:.2f}, カバレッジ={cov:.0%} ({mc}/{ts})")

            if sat_bd:
                raw_sat = sat_bd.get("raw", 0)
                parts.append(f"  安全性: {raw_sat:.1f}")
                warnings = sat_bd.get("warnings", [])
                if warnings:
                    for w in warnings:
                        parts.append(f"    ⚠ {w}")

            if res_bd:
                raw_res = res_bd.get("raw", 0)
                parts.append(f"  抵抗性: {raw_res:.1f}")
                note = res_bd.get("note", "")
                if note:
                    parts.append(f"    ℹ {note}")

    # ---- ブリッジ通過履歴（全候補） ----
    if line_traces:
        parts.append("")
        parts.append("【ブリッジ通過履歴（全候補）】")
        for lt in line_traces[:20]:  # 最大20薬剤分
            pname = lt.get("pesticide_name", lt.get("pesticide", "unknown"))
            levels = lt.get("levels", [])
            weights = lt.get("weights", [])
            blocked = lt.get("blocked", False)
            blocked_at = lt.get("blocked_at")

            trace_parts = []
            for i, (lbl, w) in enumerate(zip(_BRIDGE_LABELS, weights)):
                if w == 0.0:
                    trace_parts.append(f"{lbl}=BLOCKED")
                elif w < 1.0:
                    trace_parts.append(f"{lbl}=ATTENUATE(w={w:.1f})")
                else:
                    trace_parts.append(f"{lbl}=PASS")

            status_marker = " [!!]" if blocked else ""
            parts.append(f"  {pname}: {' '.join(trace_parts)}{status_marker}")
            if blocked_at:
                parts.append(f"    ↳ {blocked_at} でブロック")

    elif bridge_trace:
        # レガシー: 単一のtraceのみ
        parts.append("")
        parts.append("【ブリッジ通過履歴】")
        parts.append(bridge_trace)

    # ---- 代替案 ----
    if alternatives:
        parts.append("")
        parts.append("【代替案】")
        for alt in alternatives[:5]:
            alt_names = "、".join(p["name"] for p in alt.get("pesticides", []))
            alt_score = alt.get("score", 0)
            alt_mr = alt.get("mirrorId", 0)
            parts.append(f"{alt.get('rank', '?')}位: {alt_names} (スコア={alt_score:.1f}, ミラーID={alt_mr:.2f})")

    # ---- 除外された薬剤 ----
    if excluded_drugs or excluded_combos:
        parts.append("")
        parts.append("【除外された薬剤】")
        for d in excluded_drugs[:10]:
            parts.append(f"- {d}")
        for c in excluded_combos[:10]:
            parts.append(f"- {c}（2剤セット）")

    return "\n".join(parts)


# =====================================================================
# cron 投射 — build_slack_text
# =====================================================================
# 元: scripts/rx_prescribe.py build_slack_text（⑨ 投射）。
# 防除暦1行分（RBP結果付き）を短い Slack 文に整形する。


def build_slack_text(row, set_label, rbp, best_pests, alt_count):
    """
    cron ⑨ 投射 — 防除暦1行分を Slack 向け短文に整形する。

    Args:
        row: sqlite3.Row（spray_schedule 行。schedule_date / notes を参照）。
        set_label: "セットN"。
        rbp: RBP エンジンの戻りJSON（best / mirrorId 等）。
        best_pests: 処方された薬剤リスト（name / dilutionRate 等）。
        alt_count: 代替案数。

    Returns:
        Slack に送る文章文字列。
    """
    lines = [f"📅 {row['schedule_date']} 防除予定（{set_label}）"]
    if row["notes"]:
        lines.append(f"🐛 {row['notes']}")
    if best_pests:
        lines.append("💊 処方:")
        for p in best_pests:
            dose = f"（{p['dilutionRate']}）" if p.get("dilutionRate") else ""
            lines.append(f"   ・{p['name'] or p.get('id')}{dose}")
    else:
        lines.append("💊 処方: 該当薬剤なし")
    best = rbp.get("best") or {}
    mirror = best.get("mirrorId")
    if isinstance(mirror, (int, float)):
        lines.append(f"🪞 ミラーID {mirror:.4f}")
    score = best.get("totalScore")
    lines.append(f"📊 スコア {score if score is not None else '-'} / 代替 {alt_count}案")
    return "\n".join(lines)
