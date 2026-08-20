#!/usr/bin/env python3
"""
test_rbp_engine.py — Unit tests for the Python RBP algebraic engine.

Covers:
  - rbp_types.EntryVector construction/validation
  - core.run_line_through_bridges (pass / block / attenuate, bridge-level validation)
  - bridges.spec_bridges (each of the 6 gates) + set-level mixing conflict
  - main.match_eval_box / compute_target_matches / build_prescription
  - api.prescribe input validation

Run: python3 -m unittest test_rbp_engine -v   (from rbp-algebra-python/)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbp_types import (
    EntryVector, Pesticide, ToxicityClass, BridgeContext,
    is_blocked, is_flowing,
)
from core import run_line_through_bridges, validate_bridges
from bridges import spec_bridges, set_has_internal_mixing_conflict
from main import (
    sample_pesticides, sample_eval_boxes, match_eval_box,
    compute_target_matches, build_prescription, empty_safety_ctx,
    EXAMPLE_ENTRY_VECTOR,
)
from rbp_types import PrescriptionStatus
import api as rbp_api


def make_pesticide(pid, name, target_indices, toxicity=ToxicityClass.NON_TOXIC,
                    system_code="TST", system_name="TestSystem",
                    mixing_ban_targets=None, max_apps=10, phi=7):
    vec = [0] * 10
    for i in target_indices:
        vec[i] = 1
    return Pesticide(pid, name, EntryVector.from_list(vec), max_apps, phi,
                      toxicity, system_code, system_name, mixing_ban_targets or [])


def make_ctx(pesticide, ev, **overrides):
    defaults = dict(
        pesticide=pesticide,
        entry_vector=ev,
        target_match=sum(min(a, b) for a, b in zip(pesticide.target_vector.data, ev.data)),
        usage_state={},
        last_spray_date=None,
        last_pesticide_ids=[],
        last_pesticides=[],
        interval_days=None,
        rotation_state={},
    )
    defaults.update(overrides)
    return BridgeContext(**defaults)


class EntryVectorTests(unittest.TestCase):
    def test_valid_construction(self):
        ev = EntryVector.from_list([1, 0, 1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(ev.active_count, 2)

    def test_wrong_length_returns_none(self):
        self.assertIsNone(EntryVector.from_list([1, 0, 0]))

    def test_non_binary_values_raise(self):
        with self.assertRaises(AssertionError):
            EntryVector((2, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    def test_wrong_dimension_raises(self):
        with self.assertRaises(AssertionError):
            EntryVector((1, 0, 0))


class CoreEngineTests(unittest.TestCase):
    """Verify the fold engine matches the Haskell reference (Data.RBP.Core):
    hadamard uses `round`, not truncation, so weight=0.7 attenuates rather
    than fully blocking."""

    def setUp(self):
        self.ev = EntryVector.from_list([0, 1, 1, 1, 0, 1, 0, 0, 0, 0])
        self.pesticides = sample_pesticides()
        self.by_name = {p.name: p for p in self.pesticides}

    def test_full_pass_reaches_end_flowing(self):
        p = self.by_name["Benepia"]
        ctx = make_ctx(p, self.ev)
        result = run_line_through_bridges(self.ev, spec_bridges, ctx)
        self.assertTrue(is_flowing(result.state))
        self.assertEqual(len(result.trace), 6)

    def test_target_mismatch_blocks_at_l1(self):
        p = self.by_name["Dantron"]  # targets anthracnose + cutworm, not in self.ev
        ctx = make_ctx(p, self.ev)
        result = run_line_through_bridges(self.ev, spec_bridges, ctx)
        self.assertTrue(is_blocked(result.state))
        self.assertEqual(result.state.bridge_id, "SPEC-BRIDGE-TARGET")
        self.assertEqual(len(result.trace), 1)

    def test_usage_limit_blocks_at_l2(self):
        p = self.by_name["Larry"]  # max_applications=3
        ctx = make_ctx(p, self.ev, usage_state={"P47": 3})
        result = run_line_through_bridges(self.ev, spec_bridges, ctx)
        self.assertTrue(is_blocked(result.state))
        self.assertEqual(result.state.bridge_id, "SPEC-BRIDGE-USAGE")

    def test_toxicity_attenuates_not_blocks(self):
        """Regression test: weight=0.7 must round to 1 (survive), matching
        the Haskell `round` semantics — not truncate to 0 like int() did."""
        p = self.by_name["Ablame"]  # HIGHLY_TOXIC
        ctx = make_ctx(p, self.ev)
        result = run_line_through_bridges(self.ev, spec_bridges, ctx)
        self.assertTrue(is_flowing(result.state), "toxicity should attenuate, not block")
        toxicity_trace = next(t for t in result.trace if t.bridge_id == "SPEC-BRIDGE-TOXICITY")
        self.assertAlmostEqual(toxicity_trace.weight, 0.7)
        self.assertTrue(toxicity_trace.attenuated)
        self.assertTrue(toxicity_trace.passed)

    def test_rotation_abuse_blocks_at_l4(self):
        p = self.by_name["Benepia"]  # system_code "QoI"
        ctx = make_ctx(p, self.ev, rotation_state={"QoI": 2})
        result = run_line_through_bridges(self.ev, spec_bridges, ctx)
        self.assertTrue(is_blocked(result.state))
        self.assertEqual(result.state.bridge_id, "SPEC-BRIDGE-ROTATION")

    def test_mixing_conflict_blocks_at_l5(self):
        alpha = make_pesticide("X1", "AlphaMix", [1, 2], mixing_ban_targets=["BetaMix"])
        beta = make_pesticide("X2", "BetaMix", [1, 2])
        ctx = make_ctx(alpha, self.ev, last_pesticides=[beta])
        result = run_line_through_bridges(self.ev, spec_bridges, ctx)
        self.assertTrue(is_blocked(result.state))
        self.assertEqual(result.state.bridge_id, "SPEC-BRIDGE-MIXING")

    def test_set_level_mixing_conflict_detection(self):
        alpha = make_pesticide("X1", "AlphaMix", [1, 2], mixing_ban_targets=["BetaMix"])
        beta = make_pesticide("X2", "BetaMix", [1, 2])
        gamma = make_pesticide("X3", "GammaMix", [1, 2])
        self.assertTrue(set_has_internal_mixing_conflict([alpha, beta]))
        self.assertFalse(set_has_internal_mixing_conflict([alpha, gamma]))

    def test_validate_bridges_rejects_non_increasing_levels(self):
        bad = list(spec_bridges) + [spec_bridges[0]]  # duplicate level
        with self.assertRaises(ValueError):
            validate_bridges(bad)

    def test_validate_bridges_accepts_well_formed_list(self):
        validate_bridges(spec_bridges)  # should not raise


class EvalBoxMatchingTests(unittest.TestCase):
    def setUp(self):
        self.boxes = sample_eval_boxes()

    def test_exact_match(self):
        status, detail = match_eval_box(self.boxes[0].vector, self.boxes)
        self.assertEqual(status, "MATCH")
        self.assertEqual(detail, "EB-01")

    def test_no_match_is_undefined(self):
        novel = EntryVector.from_list([1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        status, detail = match_eval_box(novel, self.boxes)
        self.assertEqual(status, "UNDEFINED")
        self.assertIsNone(detail)

    def test_duplicate_boxes_report_error(self):
        dup = self.boxes + [self.boxes[0]]
        status, detail = match_eval_box(self.boxes[0].vector, dup)
        self.assertEqual(status, "ERROR")
        self.assertIn("EB-01", detail)


class TargetMatchingTests(unittest.TestCase):
    def test_overlap_counts(self):
        pesticides = sample_pesticides()
        ev = EXAMPLE_ENTRY_VECTOR
        results = dict((p.name, m) for p, m in compute_target_matches(pesticides, ev))
        # Benepia targets gray_mold+powdery_mildew, both active in EXAMPLE_ENTRY_VECTOR
        self.assertEqual(results["Benepia"], 2)
        # Dantron targets anthracnose+cutworm, neither active
        self.assertEqual(results["Dantron"], 0)


class BuildPrescriptionTests(unittest.TestCase):
    def setUp(self):
        self.pesticides = sample_pesticides()
        self.ev = EXAMPLE_ENTRY_VECTOR

    def test_success_returns_best_and_line_traces_for_all_connected(self):
        result = build_prescription(self.ev, self.pesticides, empty_safety_ctx())
        self.assertEqual(result.status, PrescriptionStatus.SUCCESS)
        self.assertIsNotNone(result.best)
        self.assertGreater(len(result.best.pesticides), 0)
        # line_traces must cover every pesticide that passed L1 (connected),
        # not just the ones in the winning set.
        connected_names = {
            p.name for p, m in compute_target_matches(self.pesticides, self.ev) if m > 0
        }
        traced_names = {lt["pesticide_name"] for lt in result.line_traces}
        self.assertEqual(connected_names, traced_names)

    def test_no_pesticide_defined_when_nothing_targets_the_vector(self):
        # Dantron targets anthracnose(0)+cutworm(4); this vector activates
        # citrus_thrips(6)+cotton_stinkbug(7) only, so overlap is zero.
        disjoint_ev = EntryVector.from_list([0, 0, 0, 0, 0, 0, 1, 1, 0, 0])
        dantron_only = [p for p in self.pesticides if p.name == "Dantron"]
        result = build_prescription(disjoint_ev, dantron_only, empty_safety_ctx())
        self.assertEqual(result.status, PrescriptionStatus.NO_PESTICIDE_DEFINED)

    def test_all_blocked_by_constraints_when_usage_exhausted(self):
        """Regression test for the connected/flowing split bug: pesticides that
        pass L1 (target match) but are blocked downstream at every candidate
        must yield ALL_BLOCKED_BY_CONSTRAINTS (with exclusions recorded), not
        NO_PESTICIDE_DEFINED and not a NameError on the missing `Blocked` import."""
        exhausted_usage = {p.pid: 999 for p in self.pesticides}
        ctx = BridgeContext(
            pesticide=self.pesticides[0],
            entry_vector=self.ev,
            target_match=0,
            usage_state=exhausted_usage,
            last_spray_date=None,
            last_pesticide_ids=[],
            last_pesticides=[],
            interval_days=None,
            rotation_state={},
        )
        result = build_prescription(self.ev, self.pesticides, ctx)
        self.assertEqual(result.status, PrescriptionStatus.ALL_BLOCKED_BY_CONSTRAINTS)
        self.assertIsNone(result.best)
        self.assertGreater(len(result.excluded_individual), 0)
        for exc in result.excluded_individual:
            self.assertEqual(exc.bridge_id, "SPEC-BRIDGE-USAGE")

    def test_mixing_conflict_pair_excluded_from_candidate_sets(self):
        alpha = make_pesticide("X1", "AlphaMix", [1, 2], mixing_ban_targets=["BetaMix"])
        beta = make_pesticide("X2", "BetaMix", [1, 2])
        result = build_prescription(self.ev, [alpha, beta], empty_safety_ctx())
        excluded_pairs = {tuple(sorted(e.pesticide_names)) for e in result.excluded_sets}
        self.assertIn(tuple(sorted([alpha.name, beta.name])), excluded_pairs)


class ApiValidationTests(unittest.TestCase):
    def test_wrong_length_vector_is_rejected(self):
        out = rbp_api.prescribe([1, 0, 0])
        self.assertIn("error", out)

    def test_non_binary_vector_is_rejected(self):
        out = rbp_api.prescribe([2, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertIn("error", out)

    def test_valid_vector_returns_engine_payload(self):
        out = rbp_api.prescribe([0, 1, 1, 1, 0, 1, 0, 0, 0, 0])
        self.assertEqual(out["engine"], "python")
        self.assertIn(out["status"], {s.name for s in PrescriptionStatus})
        self.assertIn("lineTraces", out)
        self.assertIn("excludedIndividual", out)
        self.assertIn("excludedSets", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
