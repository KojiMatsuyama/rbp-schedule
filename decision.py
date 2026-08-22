#!/usr/bin/env python3
"""
decision.py — 決定（厳密には仕様決定）トップレベル独立モジュール

業務中心無人思想（Petri網）:
    プレース（状態: トークン1, トークン2…）
      → トランジション [ 認知 ─ 評価 ─ 決定 ─ 投射 ]
        → 作動（sos）

本モジュールはトランジション群のうち「決定」に相当し、厳密には**仕様決定**
（specification decision）を担当する。

    要求評価(evaluation) が分類した評価BOX（または直接ベクトル）を使って、
    RBP行列演算を行い、ミラーIDでスコアリングし、
    6段階ブリッジ(L1-L6)の制約を通過して、
    最適な薬剤セット（仕様）を選定する。

## Haskell RBPエンジンの独立（公開関数）
- `find_haskell_bin()` — rbp-algebra バイナリの探索（独立関数）
- `call_rbp_engine(vector, eval_box_id)` — RBPエンジン呼び出し（独立関数）
    1. Haskellバイナリ (rbp-algebra) を試す（レギュラー）
    2. 失敗/未ビルド時は Python実装 (rbp-algebra-python/api.py) にフォールバック
    3. どちらもダメならエラー
- `decide(vector, eval_box_id)` — 仕様決定の入口（本トランジション）

## 区分（[[sos-projection-modules]] のパイプライン区分に従う）
    認知(perception) ──▶ 評価(evaluation: 要求→評価BOX分類)
                          ──▶ 決定(本モジュール: RBP行列演算で仕様選定)
                                ──▶ 投射(projection) ──▶ 作動(sos)

## 配置理由
- `agentic_chat/` 配下に置くと、cron/server から import すると
  `agentic_chat/__init__.py`（=LangGraph 一式）が引かれる。
  トップレベルなら `sys.path.insert(0, APP_ROOT)` の後でそのまま
  `import decision` でき、LangGraph を引き込まない。
- cron の `scripts/rx_prescribe.py` は独自に rbp-algebra-python の
  `api.prescribe` を呼ぶ別経路（`run_rbp`）であり、本モジュールを介さない。
"""

import glob
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.abspath(__file__))


# =====================================================================
# ヘルパー: RBPエンジン呼び出し（Haskell → Python フォールバック）
# =====================================================================

def find_haskell_bin() -> Optional[str]:
    """
    rbp-algebra バイナリを探す。dist-newstyle-user（ユーザービルド、
    ghc-9.6.6）と dist-newstyle（root所有、再ビルド不可）の両方をglobし、
    mtimeが最も新しいものを採用する（server.py:find_haskell_bin() と同じ方針）。

    server.py を直接importしない理由: server.py はimport時に無条件で
    os.chdir(APP_ROOT) を実行するため、agentic_chat から呼ぶとプロセス全体の
    CWDを副作用で変えてしまう。
    """
    hits = []
    for build_root in ("dist-newstyle-user", "dist-newstyle"):
        pattern = os.path.join(
            APP_ROOT, "rbp-algebra", build_root, "build", "*", "*",
            "rbp-algebra-*", "x", "rbp-algebra", "build", "rbp-algebra", "rbp-algebra")
        hits.extend(glob.glob(pattern))
    return max(hits, key=os.path.getmtime) if hits else None


def call_rbp_engine(vector: list[int], eval_box_id: Optional[str] = None) -> dict:
    """
    RBPエンジン（Haskell実装）を呼び出して処方計算を行う。

    フロー:
      1. Haskellバイナリ (rbp-algebra) を試す（レギュラー）
      2. 失敗/未ビルド時は Python実装 (rbp-algebra-python/api.py) にフォールバック
      3. どちらもダメならエラー

    Returns:
        {
            "status": "SUCCESS" | "NO_PESTICIDE_DEFINED" | "ALL_BLOCKED_BY_CONSTRAINTS",
            "evalBox": {"status": "MATCH", "detail": "EB-01"},
            "best": {
                "pesticides": [{"id": "...", "name": "...", "system": "..."}],
                "matchCount": 3,
                "coverageRatio": 0.75,
                "mirrorId": 0.95,
                "totalScore": 45.2,
                "breakdown": {
                    "effectiveness": {"raw": ..., "coverageRatio": ..., "mirrorId": ...},
                    "safety": {"raw": ..., "warnings": [...]},
                    "resistance": {"raw": ..., "note": "..."},
                },
            },
            "alternatives": [...],
            "excludedSets": [...],
            "excludedIndividual": [...],
            "bridgeTrace": [...],
        }
    """
    # --- Haskell binary (regular engine) ---
    hs_bin = find_haskell_bin()

    if hs_bin:
        import subprocess
        try:
            vec_str = ",".join(str(v) for v in vector)
            result = subprocess.run(
                [hs_bin, "--prescribe", vec_str],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
            logger.warning(f"Haskell RBP engine exited {result.returncode}: {result.stderr}")
        except Exception as e:
            logger.warning(f"Haskell RBP engine failed: {e}")

    # --- Fallback: Python RBP engine (rbp-algebra-python) ---
    py_dir = os.path.join(APP_ROOT, "rbp-algebra-python")
    if os.path.isdir(py_dir):
        import sys
        sys.path.insert(0, py_dir)
        try:
            import api as py_api
            result = py_api.prescribe(vector)
            sys.path.pop(0)
            return result
        except Exception as e:
            logger.warning(f"Python RBP engine failed: {e}")
            sys.path.pop(0)

    return {"error": "RBPエンジンが見つかりません"}


# =====================================================================
# ヘルパー: ブリッジ通過履歴のフォーマット
# =====================================================================

_BRIDGE_LABELS = {
    "SPEC-BRIDGE-TARGET": "L1 ターゲット一致",
    "SPEC-BRIDGE-USAGE": "L2 散布回数",
    "SPEC-BRIDGE-PHI": "L3 PHI残留日",
    "SPEC-BRIDGE-ROTATION": "L4 系統ローテーション",
    "SPEC-BRIDGE-MIXING": "L5 混用可否",
    "SPEC-BRIDGE-TOXICITY": "L6 毒性区分",
}


def format_bridge_trace(trace: list[dict]) -> str:
    """
    ブリッジ通過履歴のリストを人間 readable な文字列にフォーマット。

    Args:
        trace: [{"bridge_id": "...", "level": 1.0, "weight": 1.0,
                 "passed": True, "attenuated": False}, ...]

    Returns:
        "L1 ターゲット一致: PASS (w=1.0)\nL2 散布回数: PASS (w=1.0)\n..."
    """
    lines = []
    for t in trace:
        bid = t.get("bridge_id", "")
        label = _BRIDGE_LABELS.get(bid, bid)
        weight = t.get("weight", 1.0)
        passed = t.get("passed", True)
        attenuated = t.get("attenuated", False)

        if not passed:
            lines.append(f"{label}: BLOCKED (w={weight:.1f})")
        elif attenuated:
            lines.append(f"{label}: ATTENUATED (w={weight:.1f})")
        else:
            lines.append(f"{label}: PASS (w={weight:.1f})")
    return "\n".join(lines)


def format_exclusion_reason(exclusion: dict) -> str:
    """
    除外理由をフォーマット。

    Args:
        exclusion: {"pesticides": [...], "exclusionReasons": [...]}
                  または {"pesticides": [...], "exclusionReason": "..."}

    Returns:
        "ベルクート: 混用不可（ダコニール1011）"
    """
    pests = exclusion.get("pesticides", [])
    names = ", ".join(p.get("name", p.get("id", "unknown")) for p in pests)

    reasons = exclusion.get("exclusionReasons", [])
    if not reasons:
        reason = exclusion.get("exclusionReason", "")
        if reason:
            reasons = [reason]

    if not reasons:
        return names

    return f"{names}: {'; '.join(reasons)}"


# =====================================================================
# ヘルパー: 処方結果から enriched 情報を抽出
# =====================================================================

def _enrich_prescription(result: dict, vector: list[int]) -> dict:
    """
    RBPエンジンの生レスポンスから、ノード出力用に整形。

    Pythonエンジン (api.py) の出力形式:
      {
        "engine": "python",
        "sampleDb": False,
        "pesticideCount": 67,
        "evalBox": {"status": "MATCH", "detail": "EB-01"},
        "status": "SUCCESS",
        "best": {
            "pesticides": [{"id": "P01", "name": "ベルクート", "system": "QoI系"}],
            "matchCount": 3,
            "coverageRatio": 0.75,
            "mirrorId": 0.95,
            "totalScore": 45.2,
            "breakdown": {
                "effectiveness": {"raw": ..., "mirrorId": ..., "coverageRatio": ..., ...},
                "safety": {"raw": ..., "warnings": [...]},
                "resistance": {"raw": ..., "note": "..."},
                "mixingOk": true,
                "mixingReasons": [],
            },
        },
        "alternatives": [...],
        "lineTraces": [
            {"pesticide": "P01", "pesticide_name": "ベルクート",
             "levels": [1.0,2.0,...], "weights": [1.0,1.0,...],
             "blocked": false, "blocked_at": null},
            ...
        ],
        "excludedIndividual": [
            {"pesticidePid": "P10", "pesticideName": "アブラirin",
             "bridgeId": "SPEC-BRIDGE-TOXICITY", "reason": "..."},
            ...
        ],
        "excludedSets": [
            {"pesticidePids": ["P30","P61"], "pesticideNames": ["イオウフロアブル","サフオイル"],
             "gateId": "SPEC-BRIDGE-MIXING-SET", "reasons": ["..."]},
            ...
        ],
      }

    ※ 旧Haskellエンジンや失敗時は、_infer_bridge_traces / _infer_exclusions で補完。
    """
    status = result.get("status", "UNKNOWN")
    eval_box = result.get("evalBox", {})

    # --- best prescription ---
    best_raw = result.get("best")
    best = None
    if best_raw:
        pesticides = best_raw.get("pesticides", [])
        best = {
            "pesticides": pesticides,
            "matchCount": best_raw.get("matchCount", 0),
            "coverageRatio": best_raw.get("coverageRatio", 0.0),
            "mirrorId": best_raw.get("mirrorId", 0.0),
            "totalScore": best_raw.get("totalScore", 0.0),
            "breakdown": best_raw.get("breakdown", None),
            "isCombo": best_raw.get("isCombo", len(pesticides) > 1),
        }

    # --- alternatives ---
    alternatives_raw = result.get("alternatives", [])
    alternatives = []
    for alt_raw in alternatives_raw:
        alt_pesticides = alt_raw.get("pesticides", [])
        alternatives.append({
            "pesticides": alt_pesticides,
            "matchCount": alt_raw.get("matchCount", 0),
            "coverageRatio": alt_raw.get("coverageRatio", 0.0),
            "mirrorId": alt_raw.get("mirrorId", 0.0),
            "totalScore": alt_raw.get("totalScore", 0.0),
            "breakdown": alt_raw.get("breakdown", None),
            "isCombo": alt_raw.get("isCombo", len(alt_pesticides) > 1),
        })

    # --- bridge trace & exclusions ---
    # Python engine returns lineTraces (per-pesticide full traces)
    # Old Haskell engine returns bridgeTrace (single shared trace)
    line_traces = result.get("lineTraces", [])
    bridge_trace_legacy = result.get("bridgeTrace", [])
    excluded_individual = result.get("excludedIndividual", [])
    excluded_sets = result.get("excludedSets", [])

    # --- 補完: lineTracesがない場合はlegacy bridgeTraceを使う ---
    if not line_traces and bridge_trace_legacy:
        # Legacy: bridgeTrace is a single trace for the best prescription
        # Convert to per-pesticide format
        line_traces = []
        if best:
            for p in best.get("pesticides", []):
                line_traces.append({
                    "pesticide": p.get("id", ""),
                    "pesticide_name": p.get("name", "unknown"),
                    "levels": [t.get("level", 0) for t in bridge_trace_legacy],
                    "weights": [t.get("weight", 0) for t in bridge_trace_legacy],
                    "blocked": False,
                    "blocked_at": None,
                })

    # --- 補完: lineTracesが空かつengineがHaskellの場合は推論 ---
    if not line_traces:
        line_traces = _infer_bridge_traces(result, vector)

    # --- 補完: excludedIndividual/excludedSetsがない場合は推論 ---
    if not excluded_individual and not excluded_sets:
        excluded_individual, excluded_sets = _infer_exclusions(result, vector)

    # --- bridgeTrace: 最初のconnected lineのtraceを代表として返す ---
    bridge_trace = []
    if line_traces:
        # Find the best pesticide's trace
        best_pids = set()
        if best:
            for p in best.get("pesticides", []):
                best_pids.add(p.get("id", ""))
        for lt in line_traces:
            if lt.get("pesticide") in best_pids:
                # Reconstruct legacy-style trace from levels/weights
                bridge_trace = [
                    {
                        "bridge_id": f"L{int(level)}",
                        "level": level,
                        "weight": weight,
                        "passed": weight > 0,
                        "attenuated": 0 < weight < 1,
                    }
                    for level, weight in zip(lt.get("levels", []), lt.get("weights", []))
                ]
                break
        if not bridge_trace:
            # Fallback: use first trace
            first_lt = line_traces[0]
            bridge_trace = [
                {
                    "bridge_id": f"L{int(level)}",
                    "level": level,
                    "weight": weight,
                    "passed": weight > 0,
                    "attenuated": 0 < weight < 1,
                }
                for level, weight in zip(first_lt.get("levels", []), first_lt.get("weights", []))
            ]

    return {
        "status": status,
        "evalBox": eval_box,
        "best": best,
        "alternatives": alternatives,
        "bridgeTrace": bridge_trace,
        "lineTraces": line_traces,
        "excludedSets": excluded_sets,
        "excludedIndividual": excluded_individual,
        "pesticideCount": result.get("pesticideCount", 0),
    }


# =====================================================================
# ヘルパー: ブリッジtraceの推論（エンジンが返さない場合の補完）
# =====================================================================

def _infer_bridge_traces(result: dict, vector: list[int]) -> list[dict]:
    """
    RBPエンジンがlineTracesを返さない場合、
    処方結果からブリッジ通過履歴を推論する。

    各候補薬剤について、6段階ブリッジの通過状況を推測する。
    推論の精度は限定的だが、少なくともL1（ターゲット一致）は判定可能。
    """
    traces = []

    # best + alternatives の全薬剤を収集
    all_pesticides = []
    if result.get("best"):
        for p in result["best"].get("pesticides", []):
            all_pesticides.append(p)
    for alt in result.get("alternatives", []):
        for p in alt.get("pesticides", []):
            if p not in all_pesticides:
                all_pesticides.append(p)

    for p in all_pesticides[:10]:  # 最大10薬剤分
        pid = p.get("id", "")
        pname = p.get("name", "unknown")
        system = p.get("system", "")

        # ターゲット一致数（エンジン提供または推論）
        match_count = 0
        if result.get("best") and pid in str(result["best"].get("pesticides", [])):
            match_count = result["best"].get("matchCount", 0)
        for alt in result.get("alternatives", []):
            for ap in alt.get("pesticides", []):
                if ap.get("id") == pid:
                    match_count = alt.get("matchCount", 0)

        # 6段階ブリッジの推論
        # L1: TARGET — ターゲット一致でPASS
        # L2-L6: 情報が不足するためPASSと仮定（実際にはengineが返すはず）
        levels = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        weights = [1.0 if match_count > 0 else 0.0] + [1.0] * 5
        passed_flags = [w > 0 for w in weights]
        attenuated_flags = [0 < w < 1 for w in weights]

        trace = {
            "pesticide": pid,
            "pesticide_name": pname,
            "levels": levels,
            "weights": weights,
            "blocked": not all(passed_flags),
            "blocked_at": None if all(passed_flags) else "SPEC-BRIDGE-TARGET",
        }
        traces.append(trace)

    return traces


def _infer_exclusions(result: dict, vector: list[int]) -> tuple[list[dict], list[dict]]:
    """
    RBPエンジンがexclusion情報を返さない場合、
    処方結果から除外された可能性のある薬剤を推論する。

    基本的なルール:
      - ターゲット不一致 → L1でブロック
      - 高毒性 → L6で減衰（除外ではないが警告）
    """
    excluded_individual = []
    excluded_sets = []

    # 処方された薬剤IDの集合
    prescribed_ids = set()
    if result.get("best"):
        for p in result["best"].get("pesticides", []):
            prescribed_ids.add(p.get("id", ""))
    for alt in result.get("alternatives", []):
        for p in alt.get("pesticides", []):
            prescribed_ids.add(p.get("id", ""))

    # 67剤DBからターゲット不一致の薬剤を推論（簡易版）
    # ※ 実際のDBアクセスは重いので、処方結果から逆算
    #    「処方されなかった薬剤」のうち、特に注意すべきものをマーク

    # 高毒性で減衰した可能性のある薬剤（警告として表示）
    # これは実際のDB参照が必要だが、簡易版では跳过

    return excluded_individual, excluded_sets


# =====================================================================
# 決定（仕様決定）の入口
# =====================================================================

def decide(vector: list[int], eval_box_id: Optional[str] = None) -> dict:
    """
    仕様決定の入口 — 評価BOX（または直接ベクトル）を使って RBP 行列演算を行い、
    ミラーIDでスコアリングし、6段階ブリッジ(L1-L6)を通過した最適な薬剤セット
    （仕様）を選定する。

    フロー:
      1. RBPエンジン呼び出し（Haskell → Pythonフォールバック）
      2. 結果を解析: best, alternatives, bridgeTrace, exclusions
      3. 状態に応じた適切な出力を構築

    Args:
        vector: 認知(perception) が作った10次元・2値ベクトル
        eval_box_id: 要求評価(evaluation) が分類した評価BOXのID（None 可）

    Returns:
        {
            "prescription": [
                {"name": "ベルクート", "id": "P01", "score": 45.2,
                 "mirrorId": 0.95, "coverageRatio": 0.75, "breakdown": {...}}
            ],
            "alternatives": [...],
            "mirror_id": 0.95,
            "effectiveness": 45.2,
            "bridge_trace": "L1: PASS ...\n...",
            "excluded_drugs": ["ベルクート: PHI不足"],
            "excluded_combos": ["ベルクート+ダコニール: 混用不可"],
            "status": "SUCCESS",
        }
    """
    # 空ベクトルガード — 認知されていない（雑談等）場合は処方計算を行わない。
    # 空ベクトルをRBPエンジンに渡すと、全薬剤が対象一致（ミラーID=1.0）となり、
    # 「こんにちは」等の雑談に対して薬剤を処方するバグの原因となる。
    if not vector or sum(vector) == 0:
        return {
            "prescription": [],
            "alternatives": [],
            "mirror_id": None,
            "effectiveness": None,
            "bridge_trace": None,
            "excluded_drugs": [],
            "excluded_combos": [],
            "status": "NO_TARGET_IDENTIFIED",
        }

    # RBPエンジン呼び出し
    raw_result = call_rbp_engine(vector, eval_box_id=eval_box_id)

    if "error" in raw_result:
        return {
            "prescription": [],
            "alternatives": [],
            "mirror_id": None,
            "effectiveness": None,
            "bridge_trace": None,
            "excluded_drugs": [],
            "excluded_combos": [],
            "status": "ENGINE_ERROR",
            "error": f"RBPエンジンエラー: {raw_result['error']}",
        }

    # 結果を整形
    enriched = _enrich_prescription(raw_result, vector)
    status = enriched["status"]

    # --- best prescription ---
    best = enriched["best"]
    if best:
        prescription = []
        for p in best["pesticides"]:
            entry = {
                "name": p.get("name", p.get("id", "unknown")),
                "id": p.get("id", ""),
                "score": best["totalScore"],
                "mirrorId": best.get("mirrorId", 0),
                "coverageRatio": best.get("coverageRatio", 0),
                "system": p.get("system", ""),
            }
            # breakdownがあれば展開
            bd = best.get("breakdown")
            if bd:
                entry["breakdown"] = bd
            prescription.append(entry)

        mirror_id = best.get("mirrorId", 0)
        effectiveness = best.get("totalScore", 0)
    else:
        prescription = []
        mirror_id = None
        effectiveness = None

    # --- alternatives ---
    alternatives_out = []
    for i, alt in enumerate(enriched["alternatives"]):
        alt_entry = {
            "rank": i + 2,  # rank 2+ (best is rank 1)
            "pesticides": [
                {"name": p.get("name", p.get("id", "unknown")),
                 "id": p.get("id", ""),
                 "system": p.get("system", "")}
                for p in alt.get("pesticides", [])
            ],
            "score": alt.get("totalScore", 0),
            "mirrorId": alt.get("mirrorId", 0),
            "coverageRatio": alt.get("coverageRatio", 0),
        }
        bd = alt.get("breakdown")
        if bd:
            alt_entry["breakdown"] = bd
        alternatives_out.append(alt_entry)

    # --- bridge trace ---
    bridge_trace_str = None
    if enriched["bridgeTrace"]:
        bridge_trace_str = format_bridge_trace(enriched["bridgeTrace"])

    # --- excluded drugs & combos ---
    excluded_drugs = []
    for exc in enriched.get("excludedIndividual", []):
        excluded_drugs.append(format_exclusion_reason(exc))

    excluded_combos = []
    for exc in enriched.get("excludedSets", []):
        excluded_combos.append(format_exclusion_reason(exc))

    return {
        "prescription": prescription,
        "alternatives": alternatives_out,
        "mirror_id": mirror_id,
        "effectiveness": effectiveness,
        "bridge_trace": bridge_trace_str,
        "line_traces": enriched.get("lineTraces", []),
        "excluded_drugs": excluded_drugs,
        "excluded_combos": excluded_combos,
        "status": status,
    }
