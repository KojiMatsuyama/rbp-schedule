"""
RBP Bridges — The 6-Stage SPEC-BRIDGE Waterway
================================================
Defines the 6 BRIDGE gates that form the Reflect layer.

Architecture:
  EB_VECTOR (source)
    → SPEC_LINE (per-pesticide vertical pipes)
    → L1 SPEC-BRIDGE-TARGET     target mismatch → FullBlock
    → L2 SPEC-BRIDGE-USAGE      usage limit     → FullBlock
    → L3 SPEC-BRIDGE-PHI        PHI insufficient → Attenuate 0.5
    → L4 SPEC-BRIDGE-ROTATION   rotation abuse   → Attenuate 0.3
    → L5 SPEC-BRIDGE-MIXING     mixing banned    → FullBlock
    → L6 SPEC-BRIDGE-TOXICITY   highly toxic     → Attenuate 0.7
    → flowing lines → set enumeration → SPEC_BOX
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbp_types import (
    AttenuateValue, Bridge, BridgeContext, Pesticide, ToxicityClass,
    WeightAction,
)


def _full_pass(_ctx: BridgeContext) -> WeightAction:
    return WeightAction.FULL_PASS


def _full_block(_ctx: BridgeContext) -> WeightAction:
    return WeightAction.FULL_BLOCK


def _attenuate(factor: float):
    """Return a weight_fn that produces AttenuateValue(factor)."""
    def fn(_ctx: BridgeContext) -> AttenuateValue:
        return AttenuateValue(factor)
    return fn


# =============================================================================
# The 6 SPEC-BRIDGES
# =============================================================================

spec_bridges: list[Bridge] = [
    # L1: Target matching
    Bridge(
        bridge_id="SPEC-BRIDGE-TARGET",
        level=1.0,
        description="Blocks pesticides whose target diseases don't overlap with entry vector",
        weight_fn=lambda ctx: (
            WeightAction.FULL_PASS if ctx.target_match > 0
            else WeightAction.FULL_BLOCK
        ),
        reason_fn=lambda ctx: f"{ctx.pesticide.name}: target disease does not match entry vector",
        warning_fn=lambda _: "",
    ),

    # L2: Usage limit
    Bridge(
        bridge_id="SPEC-BRIDGE-USAGE",
        level=2.0,
        description="Blocks pesticides that have reached their annual application limit",
        weight_fn=lambda ctx: (
            WeightAction.FULL_BLOCK
            if ctx.pesticide.max_applications != -1
            and ctx.usage_state.get(ctx.pesticide.pid, 0) >= ctx.pesticide.max_applications
            else WeightAction.FULL_PASS
        ),
        reason_fn=lambda ctx: (
            f"Application limit reached "
            f"({ctx.usage_state.get(ctx.pesticide.pid, 0)}/{ctx.pesticide.max_applications if ctx.pesticide.max_applications != -1 else 'unlimited'})"
        ),
        warning_fn=lambda _: "",
    ),

    # L3: PHI (Pre-Harvest Interval)
    Bridge(
        bridge_id="SPEC-BRIDGE-PHI",
        level=3.0,
        description="Attenuates pesticides where PHI residual days are insufficient",
        weight_fn=lambda ctx: (
            _attenuate(0.5)(ctx)
            if ctx.interval_days is not None and ctx.interval_days < ctx.pesticide.phi_days
            else WeightAction.FULL_PASS
        ),
        reason_fn=lambda _: "PHI check: not a blocker, only attenuator",
        warning_fn=lambda ctx: (
            f"{ctx.pesticide.name}: PHI residual days check required "
            f"(last spray {ctx.interval_days} days ago, PHI {ctx.pesticide.phi_days} days)"
            if ctx.interval_days is not None else ""
        ),
        penalty=("safety", -10.0),
    ),

    # L4: Rotation management
    Bridge(
        bridge_id="SPEC-BRIDGE-ROTATION",
        level=4.0,
        description="Attenuates pesticides in systems with excessive consecutive use",
        weight_fn=lambda ctx: (
            _attenuate(0.3)(ctx)
            if ctx.pesticide.system_code not in ("MIX", "PHYSICAL")
            and ctx.rotation_state.get(ctx.pesticide.system_code, 0) >= 2
            else WeightAction.FULL_PASS
        ),
        reason_fn=lambda ctx: (
            f"{ctx.pesticide.name}: same system ({ctx.pesticide.system_code}) "
            f"used {ctx.rotation_state.get(ctx.pesticide.system_code, 0)} times consecutively"
        ),
        warning_fn=lambda ctx: (
            f"{ctx.pesticide.name}: same system ({ctx.pesticide.system_name}) "
            f"used {ctx.rotation_state.get(ctx.pesticide.system_code, 0)} times consecutively (resistance risk)"
        ),
        penalty=("resistance", -15.0),
    ),

    # L5: Mixing compatibility
    Bridge(
        bridge_id="SPEC-BRIDGE-MIXING",
        level=5.0,
        description="Blocks pesticides that conflict with the last sprayed pesticide",
        weight_fn=lambda ctx: (
            WeightAction.FULL_BLOCK
            if any(_has_mixing_conflict(ctx.pesticide, lp) for lp in ctx.last_pesticides)
            else WeightAction.FULL_PASS
        ),
        reason_fn=lambda ctx: (
            f"{ctx.pesticide.name} cannot mix with last sprayed pesticides"
        ),
        warning_fn=lambda _: "",
    ),

    # L6: Toxicity class
    Bridge(
        bridge_id="SPEC-BRIDGE-TOXICITY",
        level=6.0,
        description="Attenuates highly toxic pesticides (discouraged but not prohibited)",
        weight_fn=lambda ctx: (
            _attenuate(0.7)(ctx)
            if ctx.pesticide.toxicity_class == ToxicityClass.HIGHLY_TOXIC
            else WeightAction.FULL_PASS
        ),
        reason_fn=lambda _: "Toxicity check: not a blocker, only attenuator",
        warning_fn=lambda ctx: f"{ctx.pesticide.name}: highly toxic classification",
        penalty=("safety", -8.0),
    ),
]


def _has_mixing_conflict(a: Pesticide, b: Pesticide) -> bool:
    """Check if two pesticides have a mixing conflict."""
    def _mentions(haystack: str, needle: str) -> bool:
        return needle in haystack or needle.lower() in haystack.lower()

    a_bans = a.mixing_ban_targets
    b_bans = b.mixing_ban_targets

    # Does A ban mixing with B's system or name?
    if any(_mentions(b.system_name, t) or _mentions(b.name, t) for t in a_bans):
        return True
    # Does B ban mixing with A's system or name?
    if any(_mentions(a.system_name, t) or _mentions(a.name, t) for t in b_bans):
        return True
    return False


def set_has_internal_mixing_conflict(pesticides: list[Pesticide]) -> bool:
    """Check if a set of pesticides has internal mixing conflicts."""
    if len(pesticides) != 2:
        return False
    return _has_mixing_conflict(pesticides[0], pesticides[1])


def run_spec_line(
    pesticide: Pesticide,
    entry_vector,  # EntryVector
    ctx: BridgeContext,
) -> FlowResult:
    """Run a single pesticide's SPEC_LINE through all 6 bridges."""
    ctx_with_p = BridgeContext(
        pesticide=pesticide,
        entry_vector=entry_vector,
        target_match=ctx.target_match,
        usage_state=ctx.usage_state,
        last_spray_date=ctx.last_spray_date,
        last_pesticide_ids=ctx.last_pesticide_ids,
        last_pesticides=ctx.last_pesticides,
        interval_days=ctx.interval_days,
        rotation_state=ctx.rotation_state,
    )
    return run_line_through_bridges(entry_vector, spec_bridges, ctx_with_p)
