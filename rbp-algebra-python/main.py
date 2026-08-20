"""
RBP Algebraic Engine — Python Demonstration
=============================================
Full 5-layer pipeline with the EB-23 scenario from 解説2.

This proves: procedural if/else → algebraic type pattern matching.
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbp_types import (
    Disease, EntryVector, EvalBox, Pesticide, ToxicityClass,
    BridgeContext, ScoreBreakdown, PrescriptionSet, PrescriptionResult,
    PrescriptionStatus, FlowResult, Blocked, is_blocked,
    ExcludedIndividual, ExcludedSet,
)
from core import run_line_through_bridges
from bridges import spec_bridges, set_has_internal_mixing_conflict


# =============================================================================
# Sample Data: Representative subset of PESTICIDE_DB (mirrors EB-23 scenario)
# =============================================================================

def sample_pesticides() -> list[Pesticide]:
    return [
        # Fungicides
        Pesticide("P01", "Berquest",
                  EntryVector.from_list([1,1,1,0,0,0,0,0,0,0]),
                  3, 7, ToxicityClass.NON_TOXIC, "QoI", "QoI系"),
        Pesticide("P15", "Benepia",
                  EntryVector.from_list([0,1,1,0,0,0,0,0,0,0]),
                  2, 14, ToxicityClass.NON_TOXIC, "QoI", "QoI系"),
        Pesticide("P21", "G-Fine",
                  EntryVector.from_list([0,1,1,0,0,0,0,0,0,0]),
                  2, 14, ToxicityClass.NON_TOXIC, "DMI", "DMI系"),
        Pesticide("P38", "Benlate",
                  EntryVector.from_list([0,1,1,0,0,0,0,0,0,0]),
                  2, 28, ToxicityClass.NON_TOXIC, "MBC", "MBC系"),

        # Acaricides / Insecticides
        Pesticide("P47", "Larry",
                  EntryVector.from_list([0,0,0,1,0,0,0,0,0,0]),
                  3, 7, ToxicityClass.NON_TOXIC, "SA", "SA系"),
        Pesticide("P49", "Agromek",
                  EntryVector.from_list([0,0,0,1,0,1,0,0,0,0]),
                  2, 14, ToxicityClass.NON_TOXIC, "Amine", "アミン系"),
        Pesticide("P53", "Coromite",
                  EntryVector.from_list([0,0,0,1,0,0,0,0,0,0]),
                  2, 7, ToxicityClass.NON_TOXIC, "Thermo", "サーモ系"),
        Pesticide("P54", "StarMite",
                  EntryVector.from_list([0,0,0,1,0,0,0,0,0,0]),
                  2, 7, ToxicityClass.NON_TOXIC, "Thermo", "サーモ系"),
        Pesticide("P41", "Afirm",
                  EntryVector.from_list([0,0,0,0,0,1,0,0,0,0]),
                  2, 7, ToxicityClass.NON_TOXIC, "Diamide", "ジアミド系"),
        Pesticide("P42", "Kotetsu",
                  EntryVector.from_list([0,0,0,0,0,1,0,0,0,0]),
                  2, 14, ToxicityClass.NON_TOXIC, "Diamide", "ジアミド系"),

        # Others
        Pesticide("P05", "Topaz",
                  EntryVector.from_list([0,1,1,0,0,0,0,0,0,0]),
                  2, 21, ToxicityClass.NON_TOXIC, "DMI", "DMI系"),
        Pesticide("P10", "Ablame",
                  EntryVector.from_list([0,0,0,1,0,1,0,0,0,0]),
                  2, 14, ToxicityClass.HIGHLY_TOXIC, "Amine", "アミン系",
                  mixing_ban_targets=["Amine"]),
        Pesticide("P20", "Dantron",
                  EntryVector.from_list([1,0,0,0,1,0,0,0,0,0]),
                  2, 14, ToxicityClass.NON_TOXIC, "Sulfur", "有機硫黄系"),
    ]


def sample_eval_boxes() -> list[EvalBox]:
    return [
        EvalBox("EB-01", EntryVector.from_list([1,0,0,0,0,0,0,0,0,0]), "Anthracnose only"),
        EvalBox("EB-02", EntryVector.from_list([0,1,0,0,0,0,0,0,0,0]), "Gray mold only"),
        EvalBox("EB-03", EntryVector.from_list([0,0,1,0,0,0,0,0,0,0]), "Powdery mildew only"),
        EvalBox("EB-04", EntryVector.from_list([0,0,0,1,0,0,0,0,0,0]), "Spider mite only"),
        EvalBox("EB-08", EntryVector.from_list([0,1,0,1,0,0,0,0,0,0]), "Gray mold + Spider mite"),
        EvalBox("EB-19", EntryVector.from_list([0,1,1,0,1,0,0,0,0,0]), "Gray mold + Powdery mildew + Cutworm"),
        EvalBox("EB-22", EntryVector.from_list([1,1,1,0,1,1,0,0,1,1]), "Complex multi-disease"),
    ]


# =============================================================================
# EB-23 Scenario: Gray mold + Powdery mildew + Spider mite + Tobacco budworm
# =============================================================================

EXAMPLE_ENTRY_VECTOR = EntryVector.from_list([0, 1, 1, 1, 0, 1, 0, 0, 0, 0])


def empty_safety_ctx(ev=None) -> BridgeContext:
    """Empty safety context — no usage history (clean slate)."""
    return BridgeContext(
        pesticide=sample_pesticides()[0],
        entry_vector=ev or EXAMPLE_ENTRY_VECTOR,
        target_match=0,
        usage_state={},
        last_spray_date=None,
        last_pesticide_ids=[],
        last_pesticides=[],
        interval_days=None,
        rotation_state={},
    )


# =============================================================================
# Layer 2: EVAL_BOX matching
# =============================================================================

def match_eval_box(ev: EntryVector, boxes: list[EvalBox]):
    """
    Match entry vector against EVAL_BOXes.
    0 matches → UNDEFINED (new boundary)
    1 match   → OK
    2+ matches → ERROR
    """
    matches = [b for b in boxes if b.matches(ev)]
    if len(matches) == 0:
        return "UNDEFINED", None
    elif len(matches) == 1:
        return "MATCH", matches[0].box_id
    else:
        return "ERROR", f"multiple matches: {[b.box_id for b in matches]}"


# =============================================================================
# Layer 3: Target matching
# =============================================================================

def compute_target_matches(pesticides: list[Pesticide], ev: EntryVector):
    """TARGET_MATRIX × entryVector — overlap count per pesticide."""
    results = []
    for p in pesticides:
        overlap = sum(min(tv, ev_d) for tv, ev_d in zip(p.target_vector.data, ev.data))
        results.append((p, overlap))
    return results


# =============================================================================
# Layer 5: Prescription set selection
# =============================================================================

def compute_union_coverage(pesticides: list[Pesticide], ev: EntryVector) -> EntryVector:
    """Union coverage: OR of each pesticide's target vector."""
    dim = len(ev.data)
    union = tuple(
        1 if any(p.target_vector.data[i] == 1 for p in pesticides) else 0
        for i in range(dim)
    )
    return EntryVector(union)


def cosine_similarity(a: EntryVector, b: EntryVector) -> float:
    """Cosine similarity between two 0/1 vectors."""
    dot = sum(x * y for x, y in zip(a.data, b.data))
    norm_a = (sum(x * x for x in a.data)) ** 0.5
    norm_b = (sum(y * y for y in b.data)) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def score_set(pesticides: list[Pesticide], ev: EntryVector) -> float:
    """Score a prescription set (simplified — no penalty extraction)."""
    union = compute_union_coverage(pesticides, ev)
    match_count = sum(u * e for u, e in zip(union.data, ev.data))
    target_sum = ev.active_count
    coverage_ratio = match_count / target_sum if target_sum > 0 else 0
    mirror_id = cosine_similarity(union, ev)
    effectiveness = mirror_id * 10 + coverage_ratio * 5
    safety_base = 20.0
    resistance_base = 15.0
    return effectiveness + safety_base + resistance_base


def build_prescription(
    ev: EntryVector,
    pesticides: list[Pesticide],
    ctx: BridgeContext,
) -> PrescriptionResult:
    """Full prescription builder with detailed scoring, traces, and exclusions."""
    NON_ROTATION_SYSTEM_CODES = ("MIX", "PHYSICAL")

    # ── Step 1: Run all SPEC_LINEs through 6 bridges ──
    line_results: list[tuple[Pesticide, FlowResult]] = []
    for p in pesticides:
        ctx_p = BridgeContext(
            pesticide=p,
            entry_vector=ev,
            target_match=sum(min(tv, ev_d) for tv, ev_d in zip(p.target_vector.data, ev.data)),
            usage_state=ctx.usage_state,
            last_spray_date=ctx.last_spray_date,
            last_pesticide_ids=ctx.last_pesticide_ids,
            last_pesticides=ctx.last_pesticides,
            interval_days=ctx.interval_days,
            rotation_state=ctx.rotation_state,
        )
        result = run_line_through_bridges(ev, spec_bridges, ctx_p)
        line_results.append((p, result))

    # ── Step 2: Classify lines ──
    # "Connected" = not blocked at L1 (TARGET). Lines blocked at L1 have no water source.
    connected = [(p, r) for p, r in line_results
                 if not (is_blocked(r.state) and r.state.bridge_id == "SPEC-BRIDGE-TARGET")]
    # "Flowing" = reached L6 (TOXICITY) — eligible for set enumeration
    flowing = [(p, r) for p, r in connected if not is_blocked(r.state)]

    # ── Step 3: Handle NO_PESTICIDE_DEFINED (all blocked at L1) ──
    if not connected:
        return PrescriptionResult(None, [], PrescriptionStatus.NO_PESTICIDE_DEFINED)

    # ── Step 4: Collect excluded individuals (blocked at L2–L6) ──
    excluded_individual: list[ExcludedIndividual] = []
    for p, r in connected:
        if is_blocked(r.state):
            blocked_state = r.state
            assert isinstance(blocked_state, Blocked)
            excluded_individual.append(ExcludedIndividual(
                pesticide_pid=p.pid,
                pesticide_name=p.name,
                bridge_id=blocked_state.bridge_id,
                reason=blocked_state.reason,
            ))

    # ── Step 5: Handle ALL_BLOCKED_BY_CONSTRAINTS (no flowing lines) ──
    if not flowing:
        return PrescriptionResult(
            None, [], PrescriptionStatus.ALL_BLOCKED_BY_CONSTRAINTS,
            excluded_individual=excluded_individual,
        )

    # ── Step 6: Pool for redundancy check (passed L2, may be blocked at L5) ──
    pool = [(p, r) for p, r in connected
            if not (is_blocked(r.state) and isinstance(r.state, Blocked)
                    and r.state.bridge_id == "SPEC-BRIDGE-USAGE")]

    # ── Step 7: Enumerate candidate sets (single + pair) ──
    pests = [p for p, _ in flowing]
    candidates = [
        [p] for p in pests
    ] + [
        [pests[i], pests[j]]
        for i in range(len(pests))
        for j in range(i + 1, len(pests))
    ]

    # ── Step 8: Set-level gate (internal mixing prohibition) ──
    excluded_sets: list[ExcludedSet] = []
    valid_sets: list[list[Pesticide]] = []
    for s in candidates:
        if len(s) == 2 and set_has_internal_mixing_conflict(s):
            # Build reason
            a, b = s
            reasons = _build_mixing_reason(a, b)
            excluded_sets.append(ExcludedSet(
                pesticide_pids=[a.pid, b.pid],
                pesticide_names=[a.name, b.name],
                gate_id="SPEC-BRIDGE-MIXING-SET",
                reasons=reasons,
            ))
        else:
            valid_sets.append(s)

    # ── Step 9: Score each valid set with full breakdown ──
    scored = []
    for s in valid_sets:
        ps = _score_set_full(s, ev, flowing, pool, NON_ROTATION_SYSTEM_CODES)
        scored.append((s, ps))

    scored.sort(key=lambda x: (-x[1].mirror_id, -x[1].total_score,
                                len(x[0]), str(x[0])))

    if not scored:
        return PrescriptionResult(
            None, [], PrescriptionStatus.ALL_BLOCKED_BY_CONSTRAINTS,
            excluded_individual=excluded_individual,
            excluded_sets=excluded_sets,
        )

    # ── Step 10: Build line_traces for output ──
    line_traces = []
    for p, r in connected:
        line_traces.append({
            "pesticide": p.pid,
            "pesticide_name": p.name,
            "levels": [t.level for t in r.trace],
            "weights": [t.weight for t in r.trace],
            "blocked": is_blocked(r.state),
            "blocked_at": r.state.bridge_id if is_blocked(r.state) else None,
        })

    # ── Step 11: Return best + top alternatives ──
    best_set, best_ps = scored[0]
    alts = [ps for _, ps in scored[1:][:10]]

    return PrescriptionResult(
        best=best_ps,
        alternatives=alts,
        status=PrescriptionStatus.SUCCESS,
        line_traces=line_traces,
        excluded_individual=excluded_individual,
        excluded_sets=excluded_sets,
    )


# =============================================================================
# Helper: Build mixing reason text
# =============================================================================

def _build_mixing_reason(a: Pesticide, b: Pesticide) -> list[str]:
    """Build human-readable mixing conflict reasons between two pesticides."""
    reasons = []
    a_bans = a.mixing_ban_targets
    b_bans = b.mixing_ban_targets

    if any(_mentions(t, b.system_name) or _mentions(t, b.name) for t in a_bans):
        reasons.append(f"{a.name}は{b.name}（{b.system_name}）と混用不可")
    if any(_mentions(t, a.system_name) or _mentions(t, a.name) for t in b_bans):
        reasons.append(f"{b.name}は{a.name}（{a.system_name}）と混用不可")
    return reasons


def _mentions(haystack: str, needle: str) -> bool:
    return needle in haystack or needle.lower() in haystack.lower()


# =============================================================================
# Helper: Full scoring with breakdown
# =============================================================================

def _score_set_full(
    pesticides: list[Pesticide],
    ev: EntryVector,
    flowing: list[tuple[Pesticide, FlowResult]],
    pool: list[tuple[Pesticide, FlowResult]],
    non_rotation_codes: tuple[str, ...],
) -> PrescriptionSet:
    """Score a prescription set with full breakdown computation."""
    # ── Effectiveness: union coverage + Mirror-ID ──
    union = compute_union_coverage(pesticides, ev)
    match_count = sum(u * e for u, e in zip(union.data, ev.data))
    target_sum = ev.active_count
    coverage_ratio = match_count / target_sum if target_sum > 0 else 0
    mirror_id = cosine_similarity(union, ev)
    effectiveness = mirror_id * 10 + coverage_ratio * 5

    # ── Gather attenuation events from line traces ──
    # Build a lookup: pesticide pid -> FlowResult
    flowing_map = {p.pid: r for p, r in flowing}

    # Safety events (L3 PHI, L6 Toxicity)
    safety_warnings = []
    safety_penalty_total = 0.0
    for p in pesticides:
        fr = flowing_map.get(p.pid)
        if not fr:
            continue
        for t in fr.trace:
            if not t.attenuated:
                continue
            # Find the bridge definition for this trace
            # We need to look up penalty info from spec_bridges
            bridge_info = _find_bridge_by_level(spec_bridges, t.level)
            if bridge_info and bridge_info.penalty:
                axis, delta = bridge_info.penalty
                if axis == "safety":
                    safety_penalty_total += delta
                    safety_warnings.append(bridge_info.warning_fn(
                        BridgeContext(
                            pesticide=p, entry_vector=ev, target_match=0,
                            usage_state={}, last_spray_date=None,
                            last_pesticide_ids=[], last_pesticides=[],
                            interval_days=None, rotation_state={},
                        )
                    ))

    safety_score = max(0, 20 + safety_penalty_total)

    # Resistance events (L4 Rotation)
    resistance_warnings = []
    resistance_penalty_total = 0.0
    resistance_note = ""
    for p in pesticides:
        fr = flowing_map.get(p.pid)
        if not fr:
            continue
        for t in fr.trace:
            if not t.attenuated:
                continue
            bridge_info = _find_bridge_by_level(spec_bridges, t.level)
            if bridge_info and bridge_info.penalty:
                axis, delta = bridge_info.penalty
                if axis == "resistance":
                    resistance_penalty_total += delta
                    resistance_warnings.append(bridge_info.warning_fn(
                        BridgeContext(
                            pesticide=p, entry_vector=ev, target_match=0,
                            usage_state={}, last_spray_date=None,
                            last_pesticide_ids=[], last_pesticides=[],
                            interval_days=None, rotation_state={},
                        )
                    ))

    # Combo adjustment for 2-dose sets
    combo_adjustment = 0
    if len(pesticides) == 2:
        a, b = pesticides
        a_rot = a.system_code not in non_rotation_codes
        b_rot = b.system_code not in non_rotation_codes
        if a_rot and b_rot:
            is_same_system = a.system_code == b.system_code
            # Check redundancy: is there a solo alternative with equal/better coverage?
            is_redundant = False
            for sp, sr in pool:
                sp_union = compute_union_coverage([sp], ev)
                sp_match = sum(u * e for u, e in zip(sp_union.data, ev.data))
                if sp_match >= match_count:
                    is_redundant = True
                    break
            if is_same_system:
                resistance_note = "同一系統の組み合わせ：抵抗性リスク低減効果なし"
                combo_adjustment = -20
            elif not is_redundant:
                resistance_note = f"異なる系統（{a.system_code}／{b.system_code}）の組み合わせ：抵抗性管理上有効"

    resistance_score = max(0, 15 + resistance_penalty_total + combo_adjustment)

    warnings = safety_warnings + resistance_warnings
    total_score = effectiveness + safety_score + resistance_score

    breakdown = ScoreBreakdown(
        effectiveness=effectiveness,
        safety=safety_score,
        resistance=resistance_score,
        coverage_ratio=coverage_ratio,
        match_count=match_count,
        target_sum=target_sum,
        mirror_id=mirror_id,
        warnings=warnings,
        resistance_note=resistance_note,
    )

    return PrescriptionSet(
        pesticides=pesticides,
        match_count=match_count,
        coverage_ratio=coverage_ratio,
        mirror_id=mirror_id,
        effectiveness_score=effectiveness,
        safety_score=safety_score,
        resistance_score=resistance_score,
        total_score=total_score,
        warnings=warnings,
        breakdown=breakdown,
    )


def _find_bridge_by_level(bridges: list, level: float):
    """Find a bridge definition by its level."""
    for b in bridges:
        if b.level == level:
            return b
    return None


# =============================================================================
# Pretty Printing
# =============================================================================

SEP = "=" * 72


def section(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def fmt_flow(result: FlowResult) -> str:
    state_str = "FLOWING [OK]" if not is_blocked(result.state) else f"BLOCKED [!!] at {result.state.bridge_id}"
    lines = [f"    L{t.level}: {t.bridge_id} w={t.weight:.1f} | {'pass' if t.passed else 'stop'}{' (attenuated)' if t.attenuated else ''}"
             for t in result.trace]
    return f"  State: {state_str}\n" + "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def main():
    print("+`" + "-" * 70 + "`+")
    print("|  RBP ALGEBRAIC ENGINE — Python Proof of Concept")
    print("|  Procedural if/else → Algebraic type pattern matching")
    print("+`" + "-" * 70 + "`+")

    pesticides = sample_pesticides()
    eval_boxes = sample_eval_boxes()
    ev = EXAMPLE_ENTRY_VECTOR

    # ===== LAYER 1: DEMAND =====
    section("LAYER 1: DEMAND — EntryVector Generation")
    print("  Scenario (EB-23): Gray Mold + Powdery Mildew +")
    print("  Spider Mite + Tobacco Budworm simultaneously active")
    print(f"  Vector: {list(ev.data)}")
    print(f"  Active dimensions: {ev.active_count}")

    # ===== LAYER 2: BRIDGE =====
    section("LAYER 2: BRIDGE — EVAL_BOX Classification")
    print("  Checking against 7 predefined EVAL_BOX boundaries...")
    status, detail = match_eval_box(ev, eval_boxes)
    if status == "UNDEFINED":
        print("  Result: UNDEFINED — new EVAL_BOX boundary detected!")
        print("  (Triggers automatic registration, as in the JS implementation)")
    elif status == "MATCH":
        print(f"  Result: MATCH — {detail}")
    else:
        print(f"  Result: ERROR — {detail}")

    # ===== LAYER 3: SPECBRIDGE =====
    section("LAYER 3: SPECBRIDGE — Target Matching (TARGET_MATRIX × entryVector)")
    print("  Computing overlap count for each pesticide...")
    print(f"  {'Pesticide':<22} {'Match':>6}")
    print(f"  {'-'*22} {'-'*6}")
    for p, m in compute_target_matches(pesticides, ev):
        print(f"  {p.name:<22} {m:>6}")

    # ===== LAYER 4: REFLECT =====
    section("LAYER 4: REFLECT — 6-Stage BRIDGE Waterway")
    print("  Running representative pesticides through the 6-gate waterway")
    print("  (Clean slate: no usage history, no PHI constraints)\n")

    demo_names = ["Benepia", "Larry", "Ablame", "Afirm"]
    demo_pests = [p for p in pesticides if p.name in demo_names]
    for p in demo_pests:
        ctx = empty_safety_ctx(ev)
        ctx = BridgeContext(
            pesticide=p,
            entry_vector=ev,
            target_match=sum(min(tv, ev_d) for tv, ev_d in zip(p.target_vector.data, ev.data)),
            usage_state={},
            last_spray_date=None,
            last_pesticide_ids=[],
            last_pesticides=[],
            interval_days=None,
            rotation_state={},
        )
        result = run_line_through_bridges(ev, spec_bridges, ctx)
        print(f"  --- {p.name} ---")
        print(fmt_flow(result))
        print()

    # ===== LAYER 5: SPEC =====
    section("LAYER 5: SPEC — Prescription Set Selection")
    print("  From flowing lines: enumerate 1-dose and 2-dose sets")
    print("  Score by Mirror-ID (cosine similarity) + tie-break\n")

    result = build_prescription(ev, pesticides, empty_safety_ctx())

    if result.status == PrescriptionStatus.SUCCESS:
        print("  Status: SUCCESS")
        if result.best:
            b = result.best
            print(f"  Best set:")
            print(f"    Pesticides: {' + '.join(p.name for p in b.pesticides)}")
            print(f"    Mirror-ID:  {b.mirror_id:.6f}")
            print(f"    Coverage:   {b.coverage_ratio:.2%}")
            print(f"    Total Score: {b.total_score:.2f}")
        print(f"  Alternatives: {len(result.alternatives)}")
    else:
        print(f"  Status: {result.status.name}")

    # Show top alternatives
    if result.alternatives:
        print("\n  Top alternatives:")
        for i, alt in enumerate(result.alternatives[:5]):
            print(f"    {i+1}. {' + '.join(p.name for p in alt.pesticides)} "
                  f"(score: {alt.total_score:.2f})")

    # ===== PROOF SUMMARY =====
    section("PROOF SUMMARY: Algebraic vs Procedural")
    print("  Original JS (procedural):")
    print("    if (usageCount >= max) return block();")
    print("    if (intervalDays < phi) return attenuate(0.5);")
    print("    if (toxicity == '劇物') return attenuate(0.7);")
    print()
    print("  Python (algebraic):")
    print("    class WeightAction(Enum):")
    print("        FULL_PASS = auto()")
    print("        FULL_BLOCK = auto()")
    print("        ATTENUATE = auto()")
    print("    weight_fn :: BridgeContext -> WeightAction  # pattern match")
    print()
    print("  Engine is a pure fold:")
    print("    for bridge in sorted_bridges:")
    print("        flow = hadamard(flow, weight_fn(bridge, ctx))")
    print("  Zero if/else in the engine. All branching is in the DATA.")
    print()
    print("  Structural invariants enforced by types:")
    print("    • FlowState = Flowing | Blocked(Bid, Reason)  — algebraic, not boolean")
    print("    • WeightAction = FULL_PASS | FULL_BLOCK | ATTENUATE  — closed set")
    print("    • EntryVector: 10 dimensions, 0/1 values  — validated at construction")
    print("    • Bridge.level: strictly increasing  — validated before execution")
    print()
    print("  " + "-" * 64)
    print("  RBP行列のビジネスロジックは、Python代数型で完全に再実装された。")
    print("  手続き型if/else → 代数型パターンマッチの証明完了。")
    print("  " + "-" * 64)


if __name__ == "__main__":
    main()
