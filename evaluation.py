#!/usr/bin/env python3
"""
evaluation.py — 評価（要求評価）トップレベル独立モジュール

業務中心無人思想（Petri網）:
    プレース（状態: トークン1, トークン2…）
      → トランジション [ 認知 ─ 評価 ─ 決定 ─ 投射 ]
        → 作動（sos）

本モジュールは第一トランジション群のうち「評価」に相当し、
**要求評価**（requirement evaluation）のみを担当する。

    認知した病害虫ベクトル（10次元・2値）を、
    事前定義された評価BOX（要求の定型パターン）と正確一致させ、
    どの要求パターンに属するかを分類する。

評価は「こういう発生があるので薬剤を選びたい」という要求を
シナリオ（評価BOX）に分類するだけで、**薬剤の選定・スコアリングはしない**。
Haskell の RBP 行列演算（ミラーID・6段階ブリッジ・スコア）は「決定」の領域であり、
本モジュールの関与しない（[[sos-projection-modules]] のパイプライン区分に従う）。

    認知(perception) ──▶ 評価(本モジュール: 要求→評価BOX分類)
                          ──▶ 決定(decision: RBP行列演算で薬剤選定)
                                ──▶ 投射(projection) ──▶ 作動(sos)

## API
- `find_eval_box(vector) -> dict` — 10次元ベクトルを全評価BOXと一致させる
- `evaluate(vector) -> dict` — 要求評価の入口（空ガード+マッチング+状態付与）

## 配置理由
- `agentic_chat/` 配下に置くと、cron/server から import すると
  `agentic_chat/__init__.py`（=LangGraph 一式）が引かれる。
  トップレベルなら `sys.path.insert(0, APP_ROOT)` の後でそのまま
  `import evaluation` でき、LangGraph を引き込まない。
- data/eval_boxes.json は APP_ROOT 相対で読む。
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.abspath(__file__))


# =====================================================================
# ヘルパー: 評価BOXの読み込み
# =====================================================================

_eval_boxes_cache: Optional[list[dict]] = None


def _load_eval_boxes() -> list[dict]:
    """
    data/eval_boxes.json から評価BOXを読み込み、キャッシュする。

    Returns:
        [{"id": "EB-01", "vector": [1,0,...], "name": "炭疽病"}, ...]
    """
    global _eval_boxes_cache
    if _eval_boxes_cache is not None:
        return _eval_boxes_cache

    path = os.path.join(APP_ROOT, "data", "eval_boxes.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("eval_boxes.json not found or invalid, using fallback")
        return []

    boxes = []
    for eid, data in raw.items():
        boxes.append({
            "id": eid,
            "vector": data["vector"],
            "name": data["name"],
        })
    _eval_boxes_cache = boxes
    return boxes


# =====================================================================
# 要求評価: 評価BOXマッチング
# =====================================================================

def find_eval_box(vector: list[int]) -> dict:
    """
    10次元ベクトルを全評価BOXと正確一致させる。

    Returns:
        {"status": "MATCH", "eval_box_id": "EB-01", "eval_box_name": "炭疽病"}
        {"status": "UNDEFINED"}
        {"status": "ERROR", "error": "複数の評価BOXが一致"}
    """
    matches = []
    for box in _load_eval_boxes():
        if box["vector"] == vector:
            matches.append(box)

    if len(matches) == 1:
        return {
            "status": "MATCH",
            "eval_box_id": matches[0]["id"],
            "eval_box_name": matches[0]["name"],
        }
    elif len(matches) > 1:
        return {
            "status": "ERROR",
            "error": f"複数の評価BOXが一致: {[m['id'] for m in matches]}",
        }
    else:
        return {"status": "UNDEFINED"}


def evaluate(vector: list[int]) -> dict:
    """
    要求評価の入口 — 認知したベクトルを評価BOXに分類する。

    「こういう発生があるので薬剤を選びたい」という要求を、
    事前に定義されたシナリオ（評価BOX）に分類するだけ。
    薬剤選定・Haskell の RBP 行列演算はしない（それは決定の領域）。

    Args:
        vector: 認知(perception) が作った10次元・2値ベクトル

    Returns:
        {
            "eval_box_id": "EB-01" | None,
            "eval_box_name": "炭疽病" | None,
            "eval_status": "matched" | "undefined" | "none" | "error",
            # "error" 時にのみ存在:
            "error": "...",
            # "none"（空ベクトル・雑談）時にのみ存在:
            "intent": "chat",
        }
    """
    # 空ベクトルガード — 病害虫が認知されていない場合は要求評価に進めない。
    # 雑談・無関係入力（「こんにちは」等）は perception 経由で
    # intent="chat" として処理されるため、ここに到達するはずはないが、
    # 念のため処方結果を返さないガードとして残す。
    if not vector or sum(vector) == 0:
        return {
            "eval_box_id": None,
            "eval_box_name": None,
            "eval_status": "none",
            "intent": "chat",
        }

    # 評価BOXと正確一致マッチング
    match = find_eval_box(vector)

    if match["status"] == "MATCH":
        return {
            "eval_box_id": match["eval_box_id"],
            "eval_box_name": match["eval_box_name"],
            "eval_status": "matched",
        }
    elif match["status"] == "UNDEFINED":
        # 未知の組み合わせ → 評価BOXなしでRBP演算に進む
        return {
            "eval_box_id": None,
            "eval_box_name": None,
            "eval_status": "undefined",
        }
    else:
        # 複数一致 → エラー
        return {
            "eval_box_id": None,
            "eval_box_name": None,
            "eval_status": "error",
            "error": match.get("error", "複数の評価BOXが一致"),
        }
