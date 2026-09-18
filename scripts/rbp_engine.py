#!/usr/bin/env python3
"""汎用 RBP エンジン（DB → RBP 行列値 の自動導出）— 病害虫STB。

境界言語自動導出（DC 移植）の実装。判断ルール（ブリッジ・スコア・BOX・認知軸・
ゲート・投射）を**すべて DB（rbp_* テーブル）から読み込む**ドメイン無依の RBP
代数であり、コードに硬直化していた判断値を一切持たない。

    DB（rbp_*）  →  RBPEngine（代数・解釈）  →  RBP 行列値

これは rbp-algebra-python（bridges.py / core.py / main.py）が「代数」であり、
spec_bridges / _score_set_full が「STB インスタンス」であるのと同型。本エンジンは
その「STB インスタンス」を DB 化し、代数が DB から RBP 行列値を自動導出することを
証明する（クローズドループ）。

DC との構造差（共通上位形式BNF.txt 第11章）:
  - specbridge=present（5層）/ candidate-selection（候補選択）
  - 10次元 bit 認知ベクトル（entry 単一軸）
  - ブリッジは述語ベース（rule_json: when 述語AST + action + penalty）
  - fold は level-ascending（Hadamard・round で core.py と一致）

タスク単位の RBP プログラム（各トランジション=自己完結 RBP）:
  STB-PEST ドメインは複数タスク（rx-select=薬剤選定 / mulch=マルチ敷設）の
  ブリッジを保持するため、--task でタスクを指定する（タスクが自己完結した
  RBP プログラムを持つ）。--task 省略=ドメイン全体（DC 等の単一タスク・後方互換）。

使い方:
  python3 scripts/rbp_engine.py --task rx-select --check                 # 薬剤選定 D1/S1 検証
  python3 scripts/rbp_engine.py --task rx-select --vector '[0,1,1,1,0,1,0,0,0,0]'
  python3 scripts/rbp_engine.py --task mulch --vector '[1,0,0]' --field 2   # マルチ敷設（在庫 field 絞）
"""

import argparse
import json
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


class RBPError(Exception):
    """DB の MODEL が不完全・不正（D1/D4 相当）。既定値で埋めない。"""


class RBPEngine:
    """DB の MODEL を読み、RBP 行列値（処方）を自動導出する代数。"""

    def __init__(self, conn, domain_id="STB-PEST", task_id=None):
        self.conn = conn
        self.domain_id = domain_id
        self.task_id = task_id  # None=ドメイン全体（後方互換）/ 指定=タスク単位の RBP プログラム
        self._load()

    # ── DB 読み込み（MODEL）──────────────────────────────
    def _load(self):
        c = self.conn
        d = c.execute("SELECT * FROM rbp_domain WHERE domain_id=?", (self.domain_id,)).fetchone()
        if not d:
            raise RBPError(f"D1: rbp_domain に {self.domain_id} が存在しない")
        self.domain = dict(d)

        # task_id 指定時はタスク単位の RBP プログラム（各トランジション=自己完結 RBP）。
        # None=ドメイン全体（薬剤選定・後方互換）。発火ゲート（rbp_spec_gate）は
        # ドメイン共通の不変条件（PK=domain_id）なので task_id では絞らない。
        where = "domain_id=?"
        params = [self.domain_id]
        if self.task_id:
            where += " AND task_id=?"
            params.append(self.task_id)
        self.axes = [dict(r) for r in c.execute(
            f"SELECT * FROM rbp_perception_axis WHERE {where} ORDER BY seq", params)]
        self.boxes = [dict(r) for r in c.execute(
            f"SELECT * FROM rbp_eval_box WHERE {where} ORDER BY id", params)]
        self.bridges = [dict(r) for r in c.execute(
            f"SELECT * FROM rbp_bridge WHERE {where} ORDER BY level", params)]
        for b in self.bridges:
            b["rule"] = json.loads(b["rule_json"]) if b.get("rule_json") else None
        conds = [dict(r) for r in c.execute(
            f"SELECT * FROM rbp_spec_condition WHERE {where} ORDER BY seq", params)]
        self.conds = {r["cond_name"]: r for r in conds}
        for r in conds:
            r["rule"] = json.loads(r["rule_json"]) if r.get("rule_json") else None
        g = c.execute("SELECT * FROM rbp_spec_gate WHERE domain_id=?", (self.domain_id,)).fetchone()
        self.gate = dict(g) if g else None
        self.projections = [dict(r) for r in c.execute(
            f"SELECT * FROM rbp_projection WHERE {where} ORDER BY id", params)]

        # 認知軸（entry・単一軸）
        self.entry_axis = self.axes[0]
        self.entry_dims = json.loads(self.entry_axis["dims"])
        norm = json.loads(self.entry_axis["norm_source"])
        self.keyword_map = norm.get("keyword_map", {})

        # スコアリング知識（rbp_spec_condition の rule_json）
        self.scoring = {
            "effectiveness": self.conds["EFFECTIVENESS"]["rule"],
            "safety": self.conds["SAFETY"]["rule"],
            "resistance": self.conds["RESISTANCE"]["rule"],
            "selection": self.conds["SELECTION"]["rule"],
            "tiebreak": self.conds["TIEBREAK"]["rule"],
        }

        # D1: 必須要素の欠落を検出（既定値で埋めない）
        if not self.axes:
            raise RBPError("D1: 認知軸が0本")
        if not self.boxes:
            raise RBPError("D1: 評価BOXが0個")
        if not self.bridges:
            raise RBPError("D1: BRIDGEが0本")
        if not self.gate:
            raise RBPError("D1: 発火ゲートなし")
        for name in ("EFFECTIVENESS", "SAFETY", "RESISTANCE", "SELECTION", "TIEBREAK"):
            if name not in self.conds or not self.conds[name].get("rule"):
                raise RBPError(f"D1: SPEC条件 {name} の rule_json が欠落")
        # S1: level 厳密単調増加
        levels = [b["level"] for b in self.bridges]
        for i in range(1, len(levels)):
            if levels[i] <= levels[i - 1]:
                raise RBPError(f"S1: BRIDGE level が単調増加でない {levels}")

    # ── 候補の DB 読み込み（事実データ・タスクの SELECTION source テーブル）──
    def load_candidates(self, ctx=None):
        """タスクの SELECTION 条件の source テーブルから候補を読み、target_vector を導出する。

        薬剤選定（rx-select）は source='engine' → pesticides テーブル（targetNames から
        target_vector）。マルチ敷設（mulch）は source='mulch_materials' → 資材の type の
        one-hot。target_vector は認知軸の keyword_map（DB の norm_source）で導出する
        （data_loader._derive_target_vector と同一の法則）。候補は統一スキーマ
        （pid/name/target_vector/stock/…）に正規化する。"""
        ctx = ctx or {}
        source = self.conds["SELECTION"]["source"]
        if source == "engine" or source == "pesticides":
            return self._load_pesticide_candidates()
        return self._load_generic_candidates(source, ctx)

    def load_pesticides(self):
        """後方互換: 薬剤候補の読み込み（decision.py のライブ経路が呼ぶ）。"""
        return self._load_pesticide_candidates()

    def _load_pesticide_candidates(self):
        """pesticides テーブルから候補（薬剤）を読み、target_vector を導出する。"""
        out = []
        for r in self.conn.execute(
                "SELECT id, name, targetNames, maxApplications, phiDays, "
                "toxicityClass, system, systemCode, mixingBanTargets FROM pesticides"):
            p = dict(r)
            names = json.loads(p.get("targetNames") or "[]")
            out.append({
                "pid": p["id"],
                "name": p["name"],
                "target_vector": self.derive_target_vector(names),
                "max_applications": self._parse_max_apps(p.get("maxApplications")),
                "phi_days": int(p.get("phiDays") or 0),
                "toxicity_class": "HIGHLY_TOXIC" if p.get("toxicityClass") == "劇物" else "NON_TOXIC",
                "system_code": p.get("systemCode") or "",
                "system_name": p.get("system") or "",
                "mixing_ban_targets": json.loads(p.get("mixingBanTargets") or "[]"),
                "stock": None,
            })
        return out

    def _load_generic_candidates(self, source, ctx):
        """汎用候補テーブル（例 mulch_materials）から候補を読み、target_vector を導出する。

        候補の type（例 白黒/黒/透明）は認知軸 keyword_map のキーそのものなので、
        **完全一致優先**で one-hot 化する（_derive_type_vector）。薬剤の targetNames
        （部分一致・derive_target_vector）とは異なる。在庫（stock）は mulch_inventory
        を material_id=候補 id で集計（field_id は ctx から絞る）。"""
        out = []
        for r in self.conn.execute(f"SELECT * FROM {source} ORDER BY id"):
            cand = dict(r)
            cand_type = str(cand.get("type") or "")
            out.append({
                "pid": cand["id"],
                "name": cand.get("name") or str(cand["id"]),
                "target_vector": self._derive_type_vector(cand_type),
                "stock": self._stock_for(cand["id"], ctx),
            })
        return out

    def _derive_type_vector(self, cand_type):
        """候補の type → 認知軸 one-hot（完全一致優先）。

        完全一致（type==kw）があればそれを 1、なければ部分一致（kw in type）を 1。
        「白黒」が「黒」の部分一致で [0,0,1] にならないよう完全一致を優先する
        （白黒マルチ=type"白黒"→[1,0,0]・黒マルチ=type"黒"→[0,0,1]）。"""
        vec = [0] * len(self.entry_dims)
        exact_idx = None
        for kw, idx in self.keyword_map.items():
            if cand_type == kw:
                exact_idx = idx
                break
        if exact_idx is not None:
            vec[exact_idx] = 1
            return vec
        for kw, idx in self.keyword_map.items():
            if kw in cand_type:
                vec[idx] = 1
        return vec

    def _stock_for(self, material_id, ctx):
        """在庫量（mulch_inventory.quantity の合計）。field_id が ctx にあれば絞る。"""
        field_id = ctx.get("field_id")
        if field_id is not None:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM mulch_inventory "
                "WHERE material_id=? AND field_id=?", (material_id, field_id)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM mulch_inventory WHERE material_id=?",
                (material_id,)).fetchone()
        return float(row[0]) if row else 0.0

    @staticmethod
    def _parse_max_apps(raw):
        """maxApplications: 'inf' → -1（無制限）、数値 → int。data_loader と同一。"""
        if isinstance(raw, str) and raw.lower() == "inf":
            return -1
        try:
            return int(raw)
        except (TypeError, ValueError):
            return -1

    # ── 認知 (DEMAND) ────────────────────────────────────
    def check_ok(self, vector):
        """認知の構文チェック（10次元・0/1）。bit ベクトルは全0でも構文は成立。"""
        n = len(self.entry_dims)
        if not isinstance(vector, list) or len(vector) != n:
            return False
        return all(v in (0, 1) for v in vector)

    def derive_target_vector(self, target_names):
        """targetNames → 10次元 0/1 ベクトル（keyword_map で・DB 駆動）。"""
        vec = [0] * len(self.entry_dims)
        for name in target_names:
            for kw, idx in self.keyword_map.items():
                if kw in name:
                    vec[idx] = 1
                    break
        return vec

    # ── 評価 (BRIDGE) — BOX 完全一致 ─────────────────────
    def match_eval_box(self, vector):
        """entry ベクトルを評価BOXに割り当てる（完全一致）。

        0 → UNDEFINED（自動登録対象）/ 1 → MATCH / 2+ → ERROR。
        main.match_eval_box と同一。"""
        matches = [b for b in self.boxes if json.loads(b["members"]) == vector]
        if len(matches) == 0:
            return "UNDEFINED", None
        if len(matches) == 1:
            return "MATCH", matches[0]["box_id"]
        return "ERROR", f"multiple matches: {[b['box_id'] for b in matches]}"

    # ── 反射 (REFLECT) — 述語ブリッジの fold ─────────────
    # 型付き述語 AST（DC rbp_engine._eval_when 互換＋STB拡張 op）。
    # eval() を使わず、許可変数集合上のインタプリタで評価する。
    _ALLOWED_VARS = ("target_match", "max_applications", "usage", "interval_days",
                     "phi_days", "system_code", "rotation", "mixing_conflict",
                     "highly_toxic", "stock")
    _CMP_OPS = {
        ">=": lambda a, b: a >= b,
        ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b,
        "<": lambda a, b: a < b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }

    def _eval_when(self, node, ctx):
        """when 述語 AST の評価（型付き・eval 不使用）。

        node:
          - "default"                          : 常に真
          - {"op":"and"|"or"|"not","args":[...]}
          - {"op":"in","var":v,"values":[...]}
          - {"op":"not-in","var":v,"values":[...]}   # STB拡張
          - {"op":"not-null","var":v}                     # STB拡張
          - {"op":"truthy","var":v}
          - {"op":">="|">"|"<="|"<"|"=="|"!=","var":v,"const":c|literal}
        var は許可変数、const は数値リテラルまたは ctx 変数名（例 "phi_days"）。
        """
        if node == "default":
            return True
        if not isinstance(node, dict):
            raise RBPError(f"D4: when 述語が不正（dict 期待）: {node!r}")
        op = node.get("op")
        if op in ("and", "or"):
            args = node.get("args")
            if not isinstance(args, list) or not args:
                raise RBPError(f"D4: {op} の args が不正: {node!r}")
            return (all(self._eval_when(a, ctx) for a in args) if op == "and"
                    else any(self._eval_when(a, ctx) for a in args))
        if op == "not":
            args = node.get("args")
            if not isinstance(args, list) or len(args) != 1:
                raise RBPError(f"D4: not の args が不正: {node!r}")
            return not self._eval_when(args[0], ctx)
        if op == "truthy":
            var = node.get("var")
            if var not in self._ALLOWED_VARS:
                raise RBPError(f"D4: truthy の var が許可外: {var!r}")
            return bool(ctx.get(var))
        if op == "not-null":
            var = node.get("var")
            if var not in self._ALLOWED_VARS:
                raise RBPError(f"D4: not-null の var が許可外: {var!r}")
            return ctx.get(var) is not None
        if op == "in":
            var, values = node.get("var"), node.get("values")
            if var not in self._ALLOWED_VARS or not isinstance(values, list):
                raise RBPError(f"D4: in の指定が不正: {node!r}")
            return ctx.get(var) in values
        if op == "not-in":
            var, values = node.get("var"), node.get("values")
            if var not in self._ALLOWED_VARS or not isinstance(values, list):
                raise RBPError(f"D4: not-in の指定が不正: {node!r}")
            return ctx.get(var) not in values
        if op in self._CMP_OPS:
            var = node.get("var")
            if var not in self._ALLOWED_VARS:
                raise RBPError(f"D4: 比較の var が許可外: {var!r}")
            a = ctx.get(var)
            b = self._resolve(node.get("const", node.get("value")), ctx)
            if a is None:
                return False  # 未定（interval_days 等）は比較不能 → 偽
            return self._CMP_OPS[op](a, b)
        raise RBPError(f"D4: 未知の when 述語 op: {op!r}")

    def _resolve(self, name, ctx):
        """const の解決: 数値リテラルはそのまま、文字列は ctx 変数名として解決。"""
        if isinstance(name, (int, float)) and not isinstance(name, bool):
            return name
        if isinstance(name, str) and name in ctx:
            return ctx[name]
        raise RBPError(f"D4: when 述語の const 解決不能: {name!r}")

    def bridge_weight(self, rule, ctx):
        """述ブリッジの重みを導出: when 成立 → action、不成立 → else_action。

        full-pass=1.0 / full-block=0.0 / attenuate=factor。"""
        action = rule["action"] if self._eval_when(rule["when"], ctx) else rule["else_action"]
        t = action.get("type")
        if t == "full-pass":
            return 1.0
        if t == "full-block":
            return 0.0
        if t == "attenuate":
            return float(action["factor"])
        raise RBPError(f"D4: 未知の action type: {t!r}")

    def _bridge_ctx(self, pesticide, ev, target_match, ctx):
        """候補1本のブリッジ評価コンテキストを構築（BridgeContext と同型）。

        薬剤候補（rx-select）と汎用候補（mulch 等）でスキーマが異なるため、
        各変数は .get() で既定値を埋める（ブリッジが参照しない変数は無害）。"""
        sys_code = pesticide.get("system_code") or ""
        return {
            "target_match": target_match,
            "max_applications": pesticide.get("max_applications", -1),
            "usage": (ctx.get("usage_state") or {}).get(pesticide["pid"], 0),
            "interval_days": ctx.get("interval_days"),
            "phi_days": pesticide.get("phi_days", 0),
            "system_code": sys_code,
            "rotation": (ctx.get("rotation_state") or {}).get(sys_code, 0),
            "mixing_conflict": any(
                self._has_mixing_conflict(pesticide, lp)
                for lp in (ctx.get("last_pesticides") or [])),
            "highly_toxic": pesticide.get("toxicity_class") == "HIGHLY_TOXIC",
            # マルチ敷設: 在庫ブリッジ（MULCH-BRIDGE-STOCK）が stock>0 を判定。
            # 薬剤候補は stock=None（在庫ブリッジを持たないため使用されない）。
            "stock": pesticide.get("stock"),
        }

    def run_line(self, vector, pesticide, ctx):
        """薬剤1本の SPEC_LINE を L1〜L6 へ fold（level 昇順・Hadamard・round）。

        core.run_line_through_bridges と同一（round で 0.7 減衰が flow=1 として
        生存）。block は最初の遮断ブリッジで短絡。"""
        target_match = sum(min(tv, ev) for tv, ev in zip(pesticide["target_vector"], vector))
        bctx = self._bridge_ctx(pesticide, vector, target_match, ctx)
        flow = list(vector)
        trace = []
        for b in self.bridges:  # level 昇順（_load で ORDER BY level）
            w = self.bridge_weight(b["rule"], bctx)
            new_flow = [round(f * w) for f in flow]
            blocked = (sum(new_flow) == 0)
            trace.append({"bridge": b["bridge_id"], "level": b["level"], "weight": w,
                          "passed": sum(new_flow) > 0,
                          "attenuated": (not blocked) and (0 < w < 1)})
            if blocked:
                return {"flow": new_flow, "state": "blocked",
                        "bridge_id": b["bridge_id"], "trace": trace}
            flow = new_flow
        return {"flow": flow, "state": "flowing", "bridge_id": None, "trace": trace}

    # ── 混用判定（bridges._has_mixing_conflict と同一）──
    @staticmethod
    def _mentions(haystack, needle):
        return needle in haystack or needle.lower() in haystack.lower()

    def _has_mixing_conflict(self, a, b):
        """2薬剤の混用衝突（a が b の系統・名を禁止、またはその逆）。"""
        a_bans, b_bans = a["mixing_ban_targets"], b["mixing_ban_targets"]
        if any(self._mentions(b["system_name"], t) or self._mentions(b["name"], t) for t in a_bans):
            return True
        if any(self._mentions(a["system_name"], t) or self._mentions(a["name"], t) for t in b_bans):
            return True
        return False

    def _set_has_internal_mixing_conflict(self, pesticides):
        """2剤セットの内部混用衝突（L5.5 MIXING-SET ゲート）。"""
        if len(pesticides) != 2:
            return False
        return self._has_mixing_conflict(pesticides[0], pesticides[1])

    # ── 決定 (SPEC) — 候補選択 ───────────────────────────
    @staticmethod
    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = (sum(x * x for x in a)) ** 0.5
        nb = (sum(y * y for y in b)) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _union(self, target_vecs):
        dim = len(self.entry_dims)
        return [1 if any(tv[i] == 1 for tv in target_vecs) else 0 for i in range(dim)]

    def _score_set(self, pesticides, ev, flowing_map, pool):
        """候補セットのスコア（main._score_set_full の DB駆動版）。"""
        sc = self.scoring
        union = self._union([p["target_vector"] for p in pesticides])
        match_count = sum(u * e for u, e in zip(union, ev))
        target_sum = sum(ev)
        coverage_ratio = match_count / target_sum if target_sum > 0 else 0
        mirror_id = self._cosine(union, ev)
        effectiveness = mirror_id * sc["effectiveness"]["mirror_weight"] + \
            coverage_ratio * sc["effectiveness"]["coverage_weight"]

        # 減衰イベントからペナルティを収集（軸別）
        safety_penalty = 0.0
        resistance_penalty = 0.0
        for p in pesticides:
            fr = flowing_map.get(p["pid"])
            if not fr:
                continue
            for t in fr["trace"]:
                if not t["attenuated"]:
                    continue
                b = next((x for x in self.bridges if x["level"] == t["level"]), None)
                if not b or not b["rule"].get("penalty"):
                    continue
                axis, delta = b["rule"]["penalty"]
                if axis == "safety":
                    safety_penalty += delta
                elif axis == "resistance":
                    resistance_penalty += delta

        safety_score = max(sc["safety"]["floor"],
                           sc["safety"]["base"] + safety_penalty)

        # 組み合わせ調整（2剤・同一系統 → -20）
        combo = 0
        resistance_note = ""
        non_rot = sc["resistance"].get("non_rotation_codes", [])
        if len(pesticides) == 2:
            a, b = pesticides
            a_rot = a["system_code"] not in non_rot
            b_rot = b["system_code"] not in non_rot
            if a_rot and b_rot:
                is_same = a["system_code"] == b["system_code"]
                is_redundant = False
                for sp, _sr in pool:
                    sp_union = self._union([sp["target_vector"]])
                    sp_match = sum(u * e for u, e in zip(sp_union, ev))
                    if sp_match >= match_count:
                        is_redundant = True
                        break
                if is_same:
                    resistance_note = "同一系統の組み合わせ：抵抗性リスク低減効果なし"
                    combo = sc["resistance"]["combo_adjustment"]
                elif not is_redundant:
                    resistance_note = (f"異なる系統（{a['system_code']}／{b['system_code']}）"
                                       "の組み合わせ：抵抗性管理上有効")

        resistance_score = max(sc["resistance"]["floor"],
                               sc["resistance"]["base"] + resistance_penalty + combo)
        total = effectiveness + safety_score + resistance_score
        return {
            "pesticides": [p["pid"] for p in pesticides],
            "names": [p["name"] for p in pesticides],
            "match_count": match_count,
            "coverage_ratio": coverage_ratio,
            "mirror_id": mirror_id,
            "effectiveness": effectiveness,
            "safety": safety_score,
            "resistance": resistance_score,
            "total_score": total,
            "resistance_note": resistance_note,
        }

    def build_prescription(self, vector, pesticides, ctx):
        """全候補の処方導出（main.build_prescription の DB駆動版）。

        認知BOXは呼び出し側が別途 match_eval_box で取得（本メソッドは候補選択）。
        戻り値は api.prescribe の dict と同型のフィールドを持つ。"""
        ev = list(vector)
        # 接続ブリッジ（L1=最下層・target 一致）と冗長プール除外ブリッジ（L2）は
        # タスク固有の bridge_id（薬剤=SPEC-BRIDGE-TARGET/USAGE、マルチ=MULCH-BRIDGE-
        # TARGET/STOCK）を level で特定する（DB 駆動・ハードコードしない）。
        l1_id = self.bridges[0]["bridge_id"] if self.bridges else None
        l2_id = self.bridges[1]["bridge_id"] if len(self.bridges) > 1 else None
        # Step 1: 全 SPEC_LINE をブリッジへ
        line_results = [(p, self.run_line(ev, p, ctx)) for p in pesticides]
        # Step 2: 分類（L1 遮断=未接続 / 流れる=flowing）
        connected = [(p, r) for p, r in line_results
                     if not (r["state"] == "blocked" and r["bridge_id"] == l1_id)]
        flowing = [(p, r) for p, r in connected if r["state"] == "flowing"]
        # Step 3: NO_PESTICIDE_DEFINED（全 L1 遮断）
        if not connected:
            return {"status": "NO_PESTICIDE_DEFINED", "best": None, "alternatives": [],
                    "excluded_individual": [], "excluded_sets": [], "line_traces": []}
        # Step 4: 除外個体（L2〜L6 で遮断）
        excluded_individual = [
            {"pid": p["pid"], "name": p["name"], "bridge_id": r["bridge_id"]}
            for p, r in connected if r["state"] == "blocked"]
        # Step 5: ALL_BLOCKED_BY_CONSTRAINTS（flowing なし）
        if not flowing:
            return {"status": "ALL_BLOCKED_BY_CONSTRAINTS", "best": None, "alternatives": [],
                    "excluded_individual": excluded_individual, "excluded_sets": [],
                    "line_traces": self._line_traces(connected)}
        # Step 6: 冗長チェック用プール（L2 未遮断）
        pool = [(p, r) for p, r in connected
                if not (r["state"] == "blocked" and r["bridge_id"] == l2_id)]
        flowing_map = {p["pid"]: r for p, r in flowing}
        # Step 7: 候補セット列挙（SELECTION.set_sizes で・薬剤=[1,2] / マルチ=[1]）
        pests = [p for p, _ in flowing]
        set_sizes = self.scoring["selection"].get("set_sizes", [1, 2])
        candidates = []
        for k in set_sizes:
            if k == 1:
                candidates += [[p] for p in pests]
            elif k == 2:
                candidates += [[pests[i], pests[j]]
                               for i in range(len(pests)) for j in range(i + 1, len(pests))]
        # Step 8: セットレベルゲート（内部混用禁止）
        excluded_sets, valid_sets = [], []
        for s in candidates:
            if len(s) == 2 and self._set_has_internal_mixing_conflict(s):
                a, b = s
                excluded_sets.append({"pids": [a["pid"], b["pid"]],
                                      "names": [a["name"], b["name"]],
                                      "gate_id": "SPEC-BRIDGE-MIXING-SET"})
            else:
                valid_sets.append(s)
        # Step 9: スコア
        scored = [(s, self._score_set(s, ev, flowing_map, pool)) for s in valid_sets]
        # Step 10: ソート（tiebreak: mirrorId↓ total↓ set-size↑ id↑）
        scored.sort(key=lambda x: (-x[1]["mirror_id"], -x[1]["total_score"],
                                   len(x[0]), str(x[0])))
        if not scored:
            return {"status": "ALL_BLOCKED_BY_CONSTRAINTS", "best": None, "alternatives": [],
                    "excluded_individual": excluded_individual, "excluded_sets": excluded_sets,
                    "line_traces": self._line_traces(connected)}
        # Step 11: best + 代替案
        best_set, best_ps = scored[0]
        return {"status": "SUCCESS", "best": best_ps,
                "alternatives": [ps for _, ps in scored[1:][:10]],
                "excluded_individual": excluded_individual, "excluded_sets": excluded_sets,
                "line_traces": self._line_traces(connected)}

    def _line_traces(self, connected):
        return [
            {"pesticide": p["pid"], "pesticide_name": p["name"],
             "levels": [t["level"] for t in r["trace"]],
             "weights": [t["weight"] for t in r["trace"]],
             "blocked": r["state"] == "blocked",
             "blocked_at": r["bridge_id"] if r["state"] == "blocked" else None}
            for p, r in connected]

    # ── ゲート ───────────────────────────────────────────
    def is_fire(self, ok, box_status):
        """発火ゲート（モデル宣言）: pre perception-ok ∧ open box-reached。

        注: 実行時 api.prescribe は処方自体を BOX 到達でゲートしない（evalBox は
        報告・自動登録用、処方は常に実行）。本メソッドは境界言語の <Spec-Gate>
        としてのモデル宣言を保持する。クローズドループ（== 実行時）は judge が
        api.prescribe と同型に「常に処方」することで保証される。"""
        if not ok:
            return False
        return box_status == "MATCH"

    # ── 高レベル: 認知→評価→反射→決定 の全判定 ──
    def judge(self, vector, pesticides, ctx, field_id=None, as_of=None):
        """DATA（薬剤）＋MODEL を DB から読み、全判定を導出する。

        api.prescribe と同型の意味流を、判断ルールを DB から読みながら実行する。
        実行時と同様に BOX 到達で処方をゲートせず（evalBox は報告のみ）、常に
        候補選択を実行する。戻り値は api.prescribe の dict と同型（比較用）。"""
        ok = self.check_ok(vector)
        box_status, box_id = self.match_eval_box(vector)
        rx = self.build_prescription(vector, pesticides, ctx)
        best = rx["best"]
        return {
            "evalBox": {"status": box_status, "detail": box_id},
            "status": rx["status"],
            "best": ({"pesticides": [{"id": pid, "name": nm}
                                     for pid, nm in zip(best["pesticides"], best["names"])],
                      "matchCount": best["match_count"], "coverageRatio": best["coverage_ratio"],
                      "mirrorId": best["mirror_id"], "totalScore": best["total_score"]}
                     if best else None),
            "alternatives": [
                {"pesticides": [{"id": pid, "name": nm}
                                for pid, nm in zip(a["pesticides"], a["names"])],
                 "mirrorId": a["mirror_id"], "totalScore": a["total_score"]}
                for a in rx["alternatives"]],
            "lineTraces": rx["line_traces"],
            "excludedIndividual": rx["excluded_individual"],
            "excludedSets": rx["excluded_sets"],
        }


# ── CLI ──────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="汎用 RBP エンジン（DB → RBP 行列値）— STB")
    ap.add_argument("--domain", default="STB-PEST")
    ap.add_argument("--task", default=None,
                    help="タスク単位の RBP プログラム（例 rx-select / mulch）。省略=ドメイン全体")
    ap.add_argument("--vector", default=None, help="認知ベクトル JSON（次元=認知軸 dims）")
    ap.add_argument("--field", type=int, default=None, help="圃場 id（在庫 stock を絞る・マルチ用）")
    ap.add_argument("--check", action="store_true", help="D1/S1 自己検証のみ")
    ap.add_argument("--db", default=os.path.join(APP_ROOT, "data", "stb.db"))
    args = ap.parse_args()

    import db_setup
    conn = _connect(args.db)
    if not conn.execute("SELECT 1 FROM rbp_domain WHERE domain_id=?", (args.domain,)).fetchone():
        db_setup.seed_rbp_model(conn)
    try:
        eng = RBPEngine(conn, args.domain, args.task)  # D1/S1 検証は _load で実施
        label = f"{args.domain}" + (f"/{args.task}" if args.task else "")
        if args.check:
            print(f"✅ {label}: D1（全要素確定）・S1（level単調増加）検証通過")
            print(f"   軸 {len(eng.axes)} / BOX {len(eng.boxes)} / BRIDGE {len(eng.bridges)} / "
                  f"SPEC条件 {len(eng.conds)}")
            return 0
        if not args.vector:
            ap.error("--vector が必要です（--check 以外）")
        vector = json.loads(args.vector)
        ctx = {"field_id": args.field} if args.field is not None else {}
        candidates = eng.load_candidates(ctx)
        result = eng.judge(vector, candidates, ctx)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except RBPError as e:
        print(f"RBPエラー: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def _connect(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


if __name__ == "__main__":
    raise SystemExit(main())
