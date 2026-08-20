"""
RBP Algebraic Engine — JSON API wrapper
========================================
server.py の POST /api/prescribe (engine=python) から呼ばれる入口。
main.py のパイプライン（実データ67剤DB）を使い、entryVector を受け取って
処方結果を JSON 化可能な dict で返す。

CLI テスト: python3 api.py '[0,1,1,1,0,1,0,0,0,0]'
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbp_types import EntryVector, PrescriptionStatus, PrescriptionSet
from data_loader import load_pesticides, load_eval_boxes
from main import (
    match_eval_box,
    build_prescription, empty_safety_ctx,
)

VECTOR_DIM = 10

# Lazy-loaded real data (loaded once on first call)
_pesticides_cache: list | None = None
_eval_boxes_cache: list | None = None


def _get_pesticides() -> list:
    global _pesticides_cache
    if _pesticides_cache is None:
        _pesticides_cache = load_pesticides()
    return _pesticides_cache


def _get_eval_boxes() -> list:
    global _eval_boxes_cache
    if _eval_boxes_cache is None:
        _eval_boxes_cache = load_eval_boxes()
    return _eval_boxes_cache


def _set_to_dict(ps: PrescriptionSet) -> dict:
    return {
        'pesticides': [
            {'id': p.pid, 'name': p.name, 'system': p.system_name}
            for p in ps.pesticides
        ],
        'matchCount': ps.match_count,
        'coverageRatio': ps.coverage_ratio,
        'mirrorId': ps.mirror_id,
        'totalScore': ps.total_score,
        'breakdown': _breakdown_to_dict(getattr(ps, 'breakdown', None)),
    }


def _breakdown_to_dict(bd) -> dict | None:
    """Convert ScoreBreakdown to a JSON-serializable dict."""
    if bd is None:
        return None
    return {
        'effectiveness': {
            'raw': getattr(bd, 'effectiveness', 0.0),
            'mirrorId': getattr(bd, 'mirror_id', 0.0),
            'coverageRatio': getattr(bd, 'coverage_ratio', 0.0),
            'matchCount': getattr(bd, 'match_count', 0),
            'targetSum': getattr(bd, 'target_sum', 0),
        },
        'safety': {
            'raw': getattr(bd, 'safety', 0.0),
            'warnings': getattr(bd, 'warnings', []),
        },
        'resistance': {
            'raw': getattr(bd, 'resistance', 0.0),
            'note': getattr(bd, 'resistance_note', ''),
        },
        'mixingOk': getattr(bd, 'mixing_ok', True),
        'mixingReasons': getattr(bd, 'mixing_reasons', []),
    }


def prescribe(entry_vector) -> dict:
    if (not isinstance(entry_vector, list) or len(entry_vector) != VECTOR_DIM
            or any(v not in (0, 1) for v in entry_vector)):
        return {'error': f'entryVector must be a {VECTOR_DIM}-length array of 0/1'}

    ev = EntryVector.from_list(entry_vector)
    pesticides = _get_pesticides()

    eb_status, eb_detail = match_eval_box(ev, _get_eval_boxes())
    result = build_prescription(ev, pesticides, empty_safety_ctx(ev))

    return {
        'engine': 'python',
        'sampleDb': False,
        'pesticideCount': len(pesticides),
        'evalBox': {'status': eb_status, 'detail': eb_detail},
        'status': result.status.name,
        'best': _set_to_dict(result.best) if result.best else None,
        'alternatives': [_set_to_dict(a) for a in result.alternatives[:10]],
        'lineTraces': result.line_traces,
        'excludedIndividual': [
            {
                'pesticidePid': e.pesticide_pid,
                'pesticideName': e.pesticide_name,
                'bridgeId': e.bridge_id,
                'reason': e.reason,
            }
            for e in result.excluded_individual
        ],
        'excludedSets': [
            {
                'pesticidePids': e.pesticide_pids,
                'pesticideNames': e.pesticide_names,
                'gateId': e.gate_id,
                'reasons': e.reasons,
            }
            for e in result.excluded_sets
        ],
    }


if __name__ == '__main__':
    vec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else [0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    print(json.dumps(prescribe(vec), ensure_ascii=False, indent=2))
