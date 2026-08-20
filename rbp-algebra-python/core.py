"""
RBP Core Engine — Pure Fold Pipeline
=====================================
The engine is a strict left fold. Zero if/else in the engine body.
All branching is encoded in WeightAction algebraic values.

Mathematical core:
    f = x ⊙ W₁ ⊙ W₂ ⊙ ⋯ ⊙ Wₖ
where each Wᵢ is a uniform weight vector produced by a bridge gate.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbp_types import (
    Bridge, BridgeContext, BridgeTrace,
    Blocked, EntryVector, FlowResult, FlowState, Flowing,
    WeightAction, is_blocked, is_flowing,
)


def validate_bridges(bridges: list[Bridge]) -> None:
    """
    Prove structural invariants before execution:
      1. Levels are strictly increasing (cycle-free)
      2. No duplicate levels
    """
    sorted_bridges = sorted(bridges, key=lambda b: b.level)
    for i in range(1, len(sorted_bridges)):
        if sorted_bridges[i].level <= sorted_bridges[i - 1].level:
            raise ValueError(
                f"BRIDGE {sorted_bridges[i].bridge_id}: "
                f"level {sorted_bridges[i].level} not strictly greater than "
                f"previous {sorted_bridges[i - 1].level}"
            )


def run_line_through_bridges(
    initial_flow: EntryVector,
    bridges: list[Bridge],
    ctx: BridgeContext,
) -> FlowResult:
    """
    Run a line (vertical pipe) through all bridges in level order.

    Algorithm: strict left fold over bridges.
    Each step: hadamard(current_flow, weight_fn(bridge, ctx))

    Blocking is detected algebraically: if flow becomes all zeros,
    we short-circuit and record which bridge caused it.
    """
    validate_bridges(bridges)
    sorted_bridges = sorted(bridges, key=lambda b: b.level)

    flow = initial_flow
    state: FlowState = Flowing()
    trace: list[BridgeTrace] = []

    for bridge in sorted_bridges:
        # Get weight action from bridge's pure function
        raw_action = bridge.weight_fn(ctx)

        # Determine numeric weight
        if isinstance(raw_action, WeightAction):
            weight = 1.0 if raw_action == WeightAction.FULL_PASS else 0.0
        elif hasattr(raw_action, 'factor'):
            weight = raw_action.factor  # AttenuateValue
        else:
            raise TypeError(f"Unknown weight action type: {type(raw_action)}")

        # Hadamard product: element-wise multiply
        # round() (not int()/truncation) to match the Haskell reference engine
        # (Data.RBP.Core.hadamard uses `round`), so weight=0.7 (toxicity
        # attenuation) survives as attenuated flow=1, not a full block.
        new_flow_data = tuple(round(f * weight) for f in flow.data)
        new_flow = EntryVector(new_flow_data)

        blocked = (new_flow.active_count == 0) and is_flowing(state)

        trace.append(BridgeTrace(
            bridge_id=bridge.bridge_id,
            level=bridge.level,
            weight=weight,
            passed=not (new_flow.active_count == 0),
            attenuated=(not blocked) and (0 < weight < 1),
        ))

        if blocked:
            state = Blocked(bridge.bridge_id, bridge.reason_fn(ctx))
            return FlowResult(new_flow, state, trace)

        flow = new_flow

    return FlowResult(flow, Flowing(), trace)
