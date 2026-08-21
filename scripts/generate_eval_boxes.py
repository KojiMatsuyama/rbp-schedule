#!/usr/bin/env python3
"""
generate_eval_boxes.py — BNF準拠 EVAL_BOX DSL パーサー＆ジェネレーター

data/eval_boxes.dsl を読み、Line-Ref-Set から 0/1 ベクトルを自動計算し、
data/eval_boxes.json と data/eval_boxes.js を生成する。

BNF 生成規則:
  <BOX-Def> ::= "BOX-" <BOX-Id> ":" "{" <Line-Ref-Set> "}" [ "as" <String> ];
  <Line-Ref-Set> ::= "Line-" <Number> { "," "Line-" <Number> }

使用例:
  python3 scripts/generate_eval_boxes.py
"""

import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DSL_PATH = BASE_DIR / "data" / "eval_boxes.dsl"
JSON_OUT = BASE_DIR / "data" / "eval_boxes.json"
JS_OUT = BASE_DIR / "data" / "eval_boxes.js"


def parse_domain(text):
    """Domain ブロックをパースして dimensions と lines 名のリストを返す."""
    # Domain ... } のブロック全体を抽出
    dm = re.search(r'Domain\s+\w+\s*\{(.*?)\}', text, re.DOTALL)
    if not dm:
        raise ValueError("Domain ブロックが見つかりません")

    block = dm.group(1)

    dims_match = re.search(r'dimensions:\s*(\d+)', block)
    if not dims_match:
        raise ValueError("dimensions が定義されていません")
    dimensions = int(dims_match.group(1))

    # lines: [...] の中身（複数行対応）
    lines_match = re.search(r'lines:\s*\[(.+?)\]', block, re.DOTALL)
    if not lines_match:
        raise ValueError("lines が定義されていません")
    raw = lines_match.group(1)
    names = re.findall(r'"([^"]*)"', raw)
    line_names = names

    if len(line_names) == 0:
        raise ValueError(
            f"Domain パース失敗: dimensions={dimensions}, lines_count={len(line_names)}"
        )
    return dimensions, line_names


def parse_line_defs(lines):
    """Demand-Boundary の Line-Def をパースして {number: name} マップを返す."""
    line_map = {}
    in_demand = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("Demand-Boundary"):
            in_demand = True
            continue

        if in_demand and stripped == "}":
            in_demand = False
            continue

        if in_demand:
            # Line-N: 名前(type);
            m = re.match(r'(Line-\d+):\s*(\S+)\((\w+)\);', stripped)
            if m:
                line_ref = m.group(1)       # "Line-1"
                name = m.group(2)            # "炭疽病"
                dtype = m.group(3)           # "disease" or "pest"
                num = int(line_ref.split("-")[1])
                line_map[num] = {
                    "name": name,
                    "type": dtype,
                    "line_ref": line_ref,
                }

    return line_map


def parse_box_defs(lines):
    """Bridge-Boundary の BOX-Def をパースして [(box_id, line_numbers, label)] を返す."""
    box_defs = []
    in_bridge = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("Bridge-Boundary"):
            in_bridge = True
            continue

        if in_bridge and stripped.startswith("Bridge-Rule"):
            in_bridge = False
            continue

        if in_bridge and stripped.startswith("Bridge-Extension-Policy"):
            in_bridge = False
            continue

        if in_bridge:
            # BOX-XX: { Line-N, Line-M, ... } [as "label"];
            m = re.match(
                r'(BOX-\d+):\s*\{\s*([^}]+?)\s*\}'
                r'(?:\s+as\s+"([^"]*)")?'
                r';',
                stripped
            )
            if m:
                box_id = m.group(1)          # "BOX-01"
                line_refs_str = m.group(2)   # "Line-1, Line-3"
                label = m.group(3) or ""

                # Line-N を数値リストに変換
                line_nums = sorted([
                    int(x.strip().split("-")[1])
                    for x in line_refs_str.split(",")
                ])

                box_defs.append((box_id, line_nums, label))

    return box_defs


def vector_from_lines(line_nums, dimensions):
    """Line番号のリストから 0/1 ベクトルを生成（1-indexed → 0-indexed）."""
    vec = [0] * dimensions
    for num in line_nums:
        idx = num - 1  # 1-indexed → 0-indexed
        if 0 <= idx < dimensions:
            vec[idx] = 1
        else:
            raise ValueError(f"Line-{num} は次元範囲外 (1..{dimensions})")
    return vec


def validate_consistency(line_map, line_names, box_defs):
    """Line-Def と Domain.lines の整合性をチェック."""
    errors = []

    # Domain.lines の順序が Line-Def と一致するか
    for i, name in enumerate(line_names):
        line_num = i + 1
        if line_num in line_map:
            if line_map[line_num]["name"] != name:
                errors.append(
                    f"Line-{line_num}: Domain.lines='{name}' "
                    f"but Line-Def='{line_map[line_num]['name']}'"
                )
        else:
            errors.append(f"Line-{line_num} ('{name}') has no Line-Def")

    # BOX-Def の Line-Ref が存在するか
    for box_id, line_nums, _ in box_defs:
        for num in line_nums:
            if num not in line_map:
                errors.append(f"{box_id}: Line-{num} not defined in Demand-Boundary")

    return errors


def generate_json(eval_boxes_data, line_names):
    """eval_boxes.json を生成（diseases フィールドを追加）."""
    result = {}
    for box_id, entry in eval_boxes_data.items():
        vec = entry["vector"]
        diseases = [
            line_names[i] for i, v in enumerate(vec) if v == 1
        ]
        result[box_id] = {
            "vector": vec,
            "name": entry["name"],
            "diseases": diseases,
        }
    return result


def generate_js(eval_boxes_data):
    """eval_boxes.js を生成（EB_VECTORS / EB_MATRIX / EB_NAMES）."""
    # BOX-XX → EB-XX に変換
    js_ids = []
    vectors_list = []
    names_dict = {}

    for box_id in sorted(eval_boxes_data.keys()):
        eb_id = box_id.replace("BOX-", "EB-")
        js_ids.append(eb_id)
        entry = eval_boxes_data[box_id]
        vectors_list.append(entry["vector"])
        names_dict[eb_id] = entry["name"]

    lines = [
        "// data/eval_boxes.js — AUTO-GENERATED by generate_eval_boxes.py",
        "// DO NOT EDIT MANUALLY. Edit data/eval_boxes.dsl instead.",
        "",
        "// EB_VECTORS: BOX ID → 0/1 ベクトル",
        "const EB_VECTORS = {",
    ]
    for eid in js_ids:
        vec_str = ",".join(str(v) for v in vectors_list[js_ids.index(eid)])
        lines.append(f'  "{eid}": [{vec_str}],')
    lines.extend([
        "};",
        "",
        "// EB_MATRIX: 行数×次元数の行列（Method B: Broadcast-Hadamard-Reduce用）",
        "const EB_MATRIX = Object.values(EB_VECTORS);",
        "",
        "// EB_NAMES: BOX ID → 人間 readable な名前",
        "const EB_NAMES = {",
    ])
    for eid in js_ids:
        lines.append(f'  "{eid}": "{names_dict[eid]}",')
    lines.extend([
        "};",
        "",
    ])
    return "\n".join(lines)


def main():
    # --- Parse DSL ---
    dsl_text = DSL_PATH.read_text(encoding="utf-8")
    lines = dsl_text.split("\n")

    dimensions, line_names = parse_domain(dsl_text)
    line_map = parse_line_defs(lines)
    box_defs = parse_box_defs(lines)

    print(f"Domain: dimensions={dimensions}, lines={line_names}")
    print(f"Line-Defs: {len(line_map)} lines")
    print(f"BOX-Defs: {len(box_defs)} boxes")

    # --- Validate ---
    errors = validate_consistency(line_map, line_names, box_defs)
    if errors:
        print("\n❌ Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    # --- Generate vectors ---
    vectors = [vector_from_lines(lnums, dimensions) for _, lnums, _ in box_defs]

    # --- Build eval_boxes data ---
    eval_boxes_data = {}
    for (box_id, _, label), vec in zip(box_defs, vectors):
        diseases = [line_names[i] for i, v in enumerate(vec) if v == 1]
        eval_boxes_data[box_id] = {
            "vector": vec,
            "name": label,
            "diseases": diseases,
        }

    # --- Output JSON ---
    json_output = generate_json(eval_boxes_data, line_names)
    JSON_OUT.write_text(json.dumps(json_output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✅ Generated: {JSON_OUT}")

    # --- Output JS ---
    js_output = generate_js(eval_boxes_data)
    JS_OUT.write_text(js_output + "\n", encoding="utf-8")
    print(f"✅ Generated: {JS_OUT}")

    # --- Diff vs current ---
    print("\n=== Summary ===")
    for box_id in sorted(eval_boxes_data.keys()):
        entry = eval_boxes_data[box_id]
        eb_id = box_id.replace("BOX-", "EB-")
        print(f"  {eb_id}: {entry['name']} → {entry['vector']}")


if __name__ == "__main__":
    main()
