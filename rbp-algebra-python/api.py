"""
RBP Algebraic Engine — JSON API wrapper
========================================
server.py の POST /api/prescribe (engine=python) から呼ばれる入口。
main.py のデモ用パイプライン（サンプル13剤DB）をそのまま使い、
entryVector を受け取って処方結果を JSON 化可能な dict で返す。

注意: PoCエンジンであり、薬剤DBは data/pesticides.js の全67剤ではなく
main.py のサンプル13剤。スコアリングも簡略版（減衰ペナルティなし）のため、
JS版（rbp/prescription.js）と結果が異なる場合がある。

CLI テスト: python3 api.py '[0,1,1,1,0,1,0,0,0,0]'
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbp_types import EntryVector, PrescriptionStatus, PrescriptionSet
from main import (
    sample_pesticides, sample_eval_boxes, match_eval_box,
    build_prescription, empty_safety_ctx,
)

VECTOR_DIM = 10


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
    }


def prescribe(entry_vector) -> dict:
    if (not isinstance(entry_vector, list) or len(entry_vector) != VECTOR_DIM
            or any(v not in (0, 1) for v in entry_vector)):
        return {'error': f'entryVector must be a {VECTOR_DIM}-length array of 0/1'}

    ev = EntryVector.from_list(entry_vector)
    pesticides = sample_pesticides()

    eb_status, eb_detail = match_eval_box(ev, sample_eval_boxes())
    result = build_prescription(ev, pesticides, empty_safety_ctx(ev))

    return {
        'engine': 'python',
        'sampleDb': True,
        'pesticideCount': len(pesticides),
        'evalBox': {'status': eb_status, 'detail': eb_detail},
        'status': result.status.name,
        'best': _set_to_dict(result.best) if result.best else None,
        'alternatives': [_set_to_dict(a) for a in result.alternatives[:10]],
    }


if __name__ == '__main__':
    vec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else [0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    print(json.dumps(prescribe(vec), ensure_ascii=False, indent=2))
