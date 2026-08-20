#!/usr/bin/env python3
"""Convert data/*.js to JSON for Haskell engine."""
import json
import re
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(SCRIPT_DIR, '..', '..')

def convert_pesticides():
    """Parse data/pesticides.js -> JSON array."""
    path = os.path.join(ROOT, 'data', 'pesticides.js')
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract each { ... } block from PESTICIDE_DB
    # Find all objects between [ and ]; handle nested braces
    results = []
    depth = 0
    start = None
    for i, ch in enumerate(content):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                obj_str = content[start:i+1]
                obj = parse_pesticide_obj(obj_str)
                if obj:
                    results.append(obj)
                start = None
    return results

def parse_pesticide_obj(s):
    """Extract fields from a JS object literal."""
    def extract(key):
        # Try number (including Infinity)
        m = re.search(r'' + re.escape(key) + r'\s*:\s*(Infinity|-?[0-9]+\.?[0-9]*)', s)
        if m:
            val = m.group(1)
            if val == 'Infinity':
                return 'inf'
            return float(val)
        # Try string
        m = re.search(r'' + re.escape(key) + r'\s*:\s*"([^"]*)"', s)
        if m:
            return m.group(1)
        # Try array of strings
        m = re.search(r'' + re.escape(key) + r'\s*:\s*\[([^\]]*)\]', s)
        if m:
            items = re.findall(r'"([^"]*)"', m.group(1))
            return items
        # Try array of numbers
        m = re.search(r'' + re.escape(key) + r'\s*:\s*\[([^\]]*)\]', s)
        if m:
            items = re.findall(r'-?[0-9]+\.?[0-9]*', m.group(1))
            return [float(x) for x in items]
        return None

    obj = {}
    obj['id'] = extract('id')
    obj['name'] = extract('name')
    obj['activeIngredient'] = extract('activeIngredient')
    obj['category'] = extract('category')
    obj['targetVector'] = extract('targetVector')
    obj['targetNames'] = extract('targetNames')
    obj['phiDays'] = extract('phiDays')
    obj['mixingRestriction'] = extract('mixingRestriction')
    obj['mixingBanTargets'] = extract('mixingBanTargets')
    obj['maxApplications'] = extract('maxApplications')
    obj['toxicityClass'] = extract('toxicityClass')
    obj['system'] = extract('system')
    obj['systemCode'] = extract('systemCode')
    return obj

def convert_eval_boxes():
    """Parse data/eval_boxes.js -> JSON object."""
    path = os.path.join(ROOT, 'data', 'eval_boxes.js')
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Also load EB_NAMES for display names
    names = {}
    name_match = re.search(r'const\s+EB_NAMES\s*=\s*\{([^}]*)\}', content, re.DOTALL)
    if name_match:
        for km in re.finditer(r'"(EB-\d+)"\s*:\s*"([^"]*)"', name_match.group(1)):
            names[km.group(1)] = km.group(2)

    # Parse EB_VECTORS
    vec_match = re.search(r'const\s+EB_VECTORS\s*=\s*\{([^}]*)\}', content, re.DOTALL)
    if not vec_match:
        return {}

    result = {}
    for vm in re.finditer(r'"(EB-\d+)"\s*:\s*\[([^\]]+)\]', vec_match.group(1)):
        eid = vm.group(1)
        vec = [int(x.strip()) for x in vm.group(2).split(',')]
        result[eid] = {
            'vector': vec,
            'name': names.get(eid, eid)
        }
    return result

if __name__ == '__main__':
    os.makedirs(os.path.join(ROOT, 'data'), exist_ok=True)

    pesticides = convert_pesticides()
    with open(os.path.join(ROOT, 'data', 'pesticides.json'), 'w', encoding='utf-8') as f:
        json.dump(pesticides, f, ensure_ascii=False, indent=2)
    print(f'pesticides.json: {len(pesticides)} entries')

    eval_boxes = convert_eval_boxes()
    with open(os.path.join(ROOT, 'data', 'eval_boxes.json'), 'w', encoding='utf-8') as f:
        json.dump(eval_boxes, f, ensure_ascii=False, indent=2)
    print(f'eval_boxes.json: {len(eval_boxes)} entries')
