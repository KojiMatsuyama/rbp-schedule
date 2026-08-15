"""
RBP Algebraic Engine — Python Edition
======================================
Procedural if/else → Algebraic type pattern matching.

This module defines the entire RBP domain as algebraic types:
  - enum.Enum  ≡ Haskell data constructors (closed set of values)
  - dataclass  ≡ Haskell record types
  - Union types ≡ Haskell discriminated unions (sealed classes)

Every "if/else" in the original JS code becomes a pattern match
on these types. The type system enforces RBP invariants.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol, Sequence


# =============================================================================
# Disease / Pest dimensions (10-dimensional meaning space)
# =============================================================================

class Disease(Enum):
    """10 dimensional semantic boundaries."""
    ANTHRACNOSE = 0       # 炭疽病
    GRAY_MOLD = 1         # 灰色かび病
    POWDERY_MILDEW = 2    # うどんこ病
    SPIDER_MITE = 3       # ナミハダニ
    CUTWORM = 4           # ハスモンヨトウ
    TOBACCO_BUDWORM = 5   # オオタバコガ
    CITRUS_THRIPS = 6     # ミカンキイロアザミウマ
    COTTON_STINKBUG = 7   # ワタアブラムシ
    APHID = 8             # アブラムシ
    WHITEFLY = 9          # コナジラミ

    @property
    def name_jp(self) -> str:
        mapping = {
            Disease.ANTHRACNOSE: "炭疽病",
            Disease.GRAY_MOLD: "灰色かび病",
            Disease.POWDERY_MILDEW: "うどんこ病",
            Disease.SPIDER_MITE: "ナミハダニ",
            Disease.CUTWORM: "ハスモンヨトウ",
            Disease.TOBACCO_BUDWORM: "オオタバコガ",
            Disease.CITRUS_THRIPS: "ミカンキイロアザミウマ",
            Disease.COTTON_STINKBUG: "ワタアブラムシ",
            Disease.APHID: "アブラムシ",
            Disease.WHITEFLY: "コナジラミ",
        }
        return mapping[self]


DIMENSION = len(Disease)  # 10


# =============================================================================
# Entry Vector (Demand Layer)
# =============================================================================

@dataclass(frozen=True)
class EntryVector:
    """0/1 vector over the disease dimension. 1=present (water flows)."""
    data: tuple[int, ...]

    def __post_init__(self):
        assert len(self.data) == DIMENSION, \
            f"EntryVector must have {DIMENSION} dimensions, got {len(self.data)}"
        assert all(v in (0, 1) for v in self.data), \
            "EntryVector values must be 0 or 1"

    @classmethod
    def from_list(cls, values: list[int]) -> EntryVector | None:
        if len(values) != DIMENSION:
            return None
        return cls(tuple(values))

    @property
    def active_count(self) -> int:
        return sum(self.data)

    def is_active(self, disease: Disease) -> bool:
        return self.data[disease.value] == 1

    def __iter__(self):
        return iter(self.data)

    def __repr__(self) -> str:
        return f"EntryVector{self.data}"


# =============================================================================
# Weight Action — the algebraic replacement for if/else
# =============================================================================

class WeightAction(Enum):
    """
    The THREE states of a BRIDGE gate.

    This is the core proof point: the original JS code has:
        if (condition) return fullBlock(dim);
        if (other)     return attenuate(dim, 0.5);
        return fullPass(dim);

    Here, the "if/else" is replaced by choosing among three
    algebraic values. The CHOICE is determined by pattern matching
    on BridgeContext — not by control flow in the engine.
    """
    FULL_PASS = auto()       # weight = 1.0
    FULL_BLOCK = auto()      # weight = 0.0
    ATTENUATE = auto()       # 0 < weight < 1


@dataclass(frozen=True)
class AttenuateValue:
    """Wraps an attenuation factor (0 < factor < 1)."""
    factor: float

    def __post_init__(self):
        assert 0 < self.factor < 1, \
            f"Attenuation factor must be in (0,1), got {self.factor}"


def weight_value(action: WeightAction) -> float:
    """Convert WeightAction to its numeric weight. Exhaustive match."""
    match action:
        case WeightAction.FULL_PASS:
            return 1.0
        case WeightAction.FULL_BLOCK:
            return 0.0
        case WeightAction.ATTENUATE:
            # This branch is unreachable — ATTENUATE carries a factor
            raise RuntimeError("ATTENUATE must be wrapped in AttenuateValue")


# =============================================================================
# Evaluation Boxes (Bridge Layer)
# =============================================================================

@dataclass(frozen=True)
class EvalBox:
    """A semantic cluster of disease combinations."""
    box_id: str
    vector: EntryVector
    name: str

    def matches(self, ev: EntryVector) -> bool:
        return self.vector.data == ev.data


# =============================================================================
# Pesticide (SpecBridge Layer)
# =============================================================================

class ToxicityClass(Enum):
    NON_TOXIC = auto()
    TOXIC = auto()
    HIGHLY_TOXIC = auto()  # 劇物


@dataclass(frozen=True)
class Pesticide:
    """A pesticide: its target vector + usage constraints."""
    pid: str
    name: str
    target_vector: EntryVector
    max_applications: int  # -1 = unlimited
    phi_days: int
    toxicity_class: ToxicityClass
    system_code: str
    system_name: str
    mixing_ban_targets: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Pesticide({self.pid}: {self.name})"


# =============================================================================
# Bridge (Reflect Layer gates)
# =============================================================================

@dataclass(frozen=True)
class Bridge:
    """
    A BRIDGE gate: a conditional weight function over a pipeline.

    The "business logic" lives in weight_fn as a pattern match on
    BridgeContext. The engine loop performs ZERO branching beyond
    the fold.
    """
    bridge_id: str
    level: float          # strictly increasing order
    description: str
    weight_fn: callable   # (BridgeContext) -> WeightAction | AttenuateValue
    reason_fn: callable   # (BridgeContext) -> str  (when blocked)
    warning_fn: callable  # (BridgeContext) -> str  (when attenuated)
    penalty: tuple[str, float] | None = None  # (axis, delta)


@dataclass(frozen=True)
class BridgeContext:
    """Domain context passed to bridge functions."""
    pesticide: Pesticide
    entry_vector: EntryVector
    target_match: int
    usage_state: dict[str, int] = field(default_factory=dict)
    last_spray_date: int | None = None
    last_pesticide_ids: list[str] = field(default_factory=list)
    last_pesticides: list[Pesticide] = field(default_factory=list)
    interval_days: int | None = None
    rotation_state: dict[str, int] = field(default_factory=dict)


# =============================================================================
# Flow through bridges
# =============================================================================

class FlowState(ABC):
    """Algebraic sealed union: Flowing or Blocked."""
    pass


@dataclass(frozen=True)
class Flowing(FlowState):
    """Water reached the end."""
    pass


@dataclass(frozen=True)
class Blocked(FlowState):
    """Blocked at a specific bridge."""
    bridge_id: str
    reason: str


def is_blocked(state: FlowState) -> bool:
    return isinstance(state, Blocked)


def is_flowing(state: FlowState) -> bool:
    return isinstance(state, Flowing)


@dataclass
class BridgeTrace:
    bridge_id: str
    level: float
    weight: float
    passed: bool
    attenuated: bool


@dataclass
class FlowResult:
    flow: EntryVector
    state: FlowState
    trace: list[BridgeTrace] = field(default_factory=list)


# =============================================================================
# Prescription (Spec Layer)
# =============================================================================

@dataclass
class ScoreBreakdown:
    effectiveness: float
    safety: float
    resistance: float


@dataclass
class PrescriptionSet:
    pesticides: list[Pesticide]
    match_count: int
    coverage_ratio: float
    mirror_id: float
    effectiveness_score: float
    safety_score: float
    resistance_score: float
    total_score: float
    warnings: list[str] = field(default_factory=list)


class PrescriptionStatus(Enum):
    SUCCESS = auto()
    NO_PESTICIDE_DEFINED = auto()
    ALL_BLOCKED_BY_CONSTRAINTS = auto()


@dataclass
class PrescriptionResult:
    best: PrescriptionSet | None
    alternatives: list[PrescriptionSet]
    status: PrescriptionStatus
    bridge_trace: list[BridgeTrace] = field(default_factory=list)
