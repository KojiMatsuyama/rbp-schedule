"""
RBP Data Loader — Load real data from JSON files
==================================================
Mirrors rbp-algebra/src/Data/RBP/DataLoader.hs.

Loads:
  - data/pesticides.json  → list[Pesticide]  (derives targetVector from targetNames)
  - data/eval_boxes.json  → list[EvalBox]    (plus custom eval boxes)

CLI test: python3 data_loader.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbp_types import EntryVector, EvalBox, Pesticide, ToxicityClass

# ---------------------------------------------------------------------------
# Dimension index: maps Japanese disease keywords → vector index
# Mirrors the 10-dimensional meaning space defined in rbp_types.Disease
# ---------------------------------------------------------------------------

DISEASE_KEYWORD_MAP: dict[str, int] = {
    "炭疽": 0,
    "灰色かび": 1,
    "うどんこ": 2,
    "ハダニ": 3,
    "ハスモン": 4,
    "オオタバコ": 5,
    "アザミウマ": 6,
    "ワタアブラ": 7,
    "アブラムシ": 8,
    "コナジラミ": 9,
}


def _derive_target_vector(target_names: list[str]) -> list[int]:
    """Derive a 10-dim 0/1 targetVector from targetNames.

    Each target name may contain a keyword that maps to a dimension index.
    E.g. ["炭疽","うどんこ","灰色かび"] → [1,1,1,0,0,0,0,0,0,0]
    """
    vec = [0] * 10
    for name in target_names:
        for keyword, idx in DISEASE_KEYWORD_MAP.items():
            if keyword in name:
                vec[idx] = 1
                break  # one keyword match per target name is enough
    return vec


def _parse_max_applications(raw) -> int:
    """Parse maxApplications: number → int, 'inf' → -1 (unlimited)."""
    if isinstance(raw, str) and raw.lower() == "inf":
        return -1
    return int(raw)


def _parse_toxicity_class(raw: str) -> ToxicityClass:
    """Map Japanese toxicity strings to ToxicityClass enum."""
    if raw == "劇物":
        return ToxicityClass.HIGHLY_TOXIC
    # "普通物" and anything else → NON_TOXIC
    return ToxicityClass.NON_TOXIC


def load_pesticides(path: str | None = None) -> list[Pesticide]:
    """Load the full 67-pesticide database from pesticides.json.

    Derives targetVector from targetNames since the JSON stores
    targetVector as empty arrays.
    """
    if path is None:
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "..", "data", "pesticides.json")

    with open(path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    pesticides: list[Pesticide] = []
    for entry in raw_data:
        target_vec = _derive_target_vector(entry["targetNames"])
        pesticides.append(Pesticide(
            pid=entry["id"],
            name=entry["name"],
            target_vector=EntryVector(tuple(target_vec)),
            max_applications=_parse_max_applications(entry["maxApplications"]),
            phi_days=int(entry["phiDays"]),
            toxicity_class=_parse_toxicity_class(entry["toxicityClass"]),
            system_code=entry["systemCode"],
            system_name=entry["system"],
            mixing_ban_targets=entry.get("mixingBanTargets", []),
        ))

    return pesticides


def load_eval_boxes(path: str | None = None) -> list[EvalBox]:
    """Load eval boxes from eval_boxes.json (+ custom eval boxes).

    Returns a flat list of EvalBox objects.
    """
    if path is None:
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "..", "data", "eval_boxes.json")

    with open(path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    boxes: list[EvalBox] = []
    for box_id, entry in raw_data.items():
        vec = tuple(int(v) for v in entry["vector"])
        boxes.append(EvalBox(box_id, EntryVector(vec), entry["name"]))

    # Also load custom eval boxes if they exist
    custom_path = os.path.join(os.path.dirname(path), "eval_boxes_custom.json")
    if os.path.exists(custom_path):
        with open(custom_path, "r", encoding="utf-8") as f:
            custom_data = json.load(f)
        for box_id, entry in custom_data.items():
            vec = tuple(int(v) for v in entry["vector"])
            boxes.append(EvalBox(box_id, EntryVector(vec), entry["name"]))

    return boxes


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pests = load_pesticides()
    boxes = load_eval_boxes()
    print(f"Loaded {len(pests)} pesticides, {len(boxes)} eval boxes")
    for p in pests[:5]:
        print(f"  {p.pid} {p.name}: target={p.target_vector.data}, "
              f"phi={p.phi_days}, tox={p.toxicity_class.name}")
    for b in boxes[:5]:
        print(f"  {b.box_id} {b.name}: vector={b.vector.data}")
