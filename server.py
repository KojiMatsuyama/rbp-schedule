#!/usr/bin/env python3
# server.py — STBアプリ用の静的ファイル配信 + SQLite永続化サーバー。
# IP/ポート/バインド/キャッシュ無効化/ThreadingHTTPServer必須
#
# SQLite (data/stb.db) に以下のテーブルを持つ:
#   pesticides, diseases, eval_boxes, eval_boxes_custom, spray_history, spray_schedule, inventory
#
# 要求評価RBP（rbp/eval_box_registry.js）が自動登録した新しいEVAL_BOXを
# eval_boxes_custom テーブルへ永続化する。
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

# 病害虫の唯一無一の正（SQLite diseases テーブル）の bootstrap シード元。
from db_setup import DISEASES_SEED


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_activate(self):
        self.socket.listen(128)

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_ROOT)

DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
VECTOR_DIM = 10

# --- SQLite helpers ---

def get_db():
    """Return a thread-local SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pesticides (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            activeIngredient TEXT,
            category TEXT,
            targetVector TEXT,
            targetNames TEXT,
            phiDays REAL,
            mixingRestriction TEXT,
            mixingBanTargets TEXT,
            maxApplications REAL,
            toxicityClass TEXT,
            system TEXT,
            systemCode TEXT,
            dilutionRate TEXT
        );

        CREATE TABLE IF NOT EXISTS diseases (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('disease', 'pest')),
            icon TEXT
        );

        CREATE TABLE IF NOT EXISTS eval_boxes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            vector TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eval_boxes_custom (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            vector TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spray_history (
            date TEXT PRIMARY KEY,
            pests TEXT NOT NULL,
            vector TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spray_schedule (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_date   TEXT    NOT NULL,
            actual_date     TEXT,
            status          TEXT    NOT NULL DEFAULT 'scheduled'
                        CHECK(status IN ('scheduled', 'done', 'missed', 'rescheduled')),
            trigger_type    TEXT    NOT NULL DEFAULT 'cycle'
                        CHECK(trigger_type IN ('cycle', 'observation', 'forecast')),
            trigger_ref     TEXT,
            eval_box_id     TEXT REFERENCES eval_boxes(id),
            rb_out_json     TEXT,
            set_ids         TEXT    NOT NULL,
            pesticide_ids   TEXT    NOT NULL,
            operator        TEXT,
            weather         TEXT,
            notes           TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'jst'))
        );

        CREATE INDEX IF NOT EXISTS idx_spray_schedule_date ON spray_schedule(schedule_date);
        CREATE INDEX IF NOT EXISTS idx_spray_schedule_status ON spray_schedule(status);
        CREATE INDEX IF NOT EXISTS idx_spray_schedule_eval_box ON spray_schedule(eval_box_id);

        CREATE TABLE IF NOT EXISTS inventory (
            id TEXT PRIMARY KEY,
            pesticideId TEXT NOT NULL REFERENCES pesticides(id),
            productName TEXT NOT NULL,
            lotNumber TEXT,
            quantity REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'ml',
            expiryDate TEXT,
            supplier TEXT,
            purchaseDate TEXT,
            notes TEXT,
            createdAt TEXT NOT NULL,
            updatedAt TEXT NOT NULL
        );
    """)
    conn.commit()

    # 病害虫DB（唯一無一の正）の self-bootstrap: data/stb.db は gitignore されているため
    # クリーンチェックアウトでは空。diseases テーブルが空なら db_setup.py の
    # DISEASES_SEED（唯一の正）から投入し、/api/diseases と perception.py が
    # 起動時に正しく動作するよう保証する。
    _n = conn.execute("SELECT COUNT(*) FROM diseases").fetchone()[0]
    if _n == 0:
        for _d in DISEASES_SEED:
            conn.execute(
                "INSERT INTO diseases (id, name, type, icon) VALUES (?, ?, ?, ?)",
                _d,
            )
        conn.commit()

    # 薬剤DB（唯一無一の正）の self-bootstrap: data/stb.db が空なら、コミット済みの
    # 生成物 data/pesticides.json（DB から scripts/export_pesticides.py が再生成）から投入。
    # 病害虫（インライン DISEASES_SEED）と違い、薬剤は 67 件・フィールド多のため
    # 生成物スナップショットを bootstrap 源に使う（db_setup.py と同一経路）。
    _pn = conn.execute("SELECT COUNT(*) FROM pesticides").fetchone()[0]
    if _pn == 0:
        _ppath = os.path.join(APP_ROOT, "data", "pesticides.json")
        if os.path.exists(_ppath):
            with open(_ppath, "r", encoding="utf-8") as _pf:
                _ppests = json.load(_pf)
            for _p in _ppests:
                conn.execute(
                    """INSERT OR IGNORE INTO pesticides
                       (id, name, activeIngredient, category, targetVector, targetNames,
                        phiDays, mixingRestriction, mixingBanTargets, maxApplications,
                        toxicityClass, system, systemCode, dilutionRate)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _p["id"], _p["name"], _p.get("activeIngredient"), _p.get("category"),
                        json.dumps(_p.get("targetVector", [])),
                        json.dumps(_p.get("targetNames", [])),
                        _p.get("phiDays"), _p.get("mixingRestriction"),
                        json.dumps(_p.get("mixingBanTargets", [])), _p.get("maxApplications"),
                        _p.get("toxicityClass"), _p.get("system"), _p.get("systemCode"),
                        _p.get("dilutionRate"),
                    ),
                )
            conn.commit()

    conn.close()


# --- POST /api/prescribe: RBPエンジン切替（python / haskell） ---
PY_ENGINE_DIR = os.path.join(APP_ROOT, "rbp-algebra-python")
_py_engine = None  # lazy-load cache


def run_python_engine(entry_vector):
    global _py_engine
    if _py_engine is None:
        sys.path.insert(0, PY_ENGINE_DIR)
        import api as _py_engine_mod
        _py_engine = _py_engine_mod
    return _py_engine.prescribe(entry_vector)


def find_haskell_bin():
    hits = []
    for build_root in ("dist-newstyle-user", "dist-newstyle"):
        pattern = os.path.join(
            APP_ROOT, "rbp-algebra", build_root, "build", "*", "*",
            "rbp-algebra-*", "x", "rbp-algebra", "build", "rbp-algebra", "rbp-algebra")
        hits.extend(glob.glob(pattern))
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def run_haskell_engine(entry_vector):
    bin_path = find_haskell_bin()
    if bin_path is None:
        return {"error": "Haskellバイナリが見つかりません（rbp-algebra/ で cabal build が必要）"}
    csv = ",".join(str(v) for v in entry_vector)
    try:
        proc = subprocess.run(
            [bin_path, "--prescribe", csv],
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"error": "Haskellエンジンがタイムアウトしました"}
    if proc.returncode != 0:
        return {"error": f"Haskellエンジンが異常終了しました: {proc.stderr[:500]}"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": f"HaskellエンジンのJSON出力を解析できません: {proc.stdout[:200]}"}


# ─── LangGraph Token Store (Petri net model) ─────────────────────
# トークン集約ノードの状態をメモリ上に保持。
# クライアント（スケジュールタイマー / カレンダーUI）がトークンを投入。
# 全トークンが揃うまでエージェントは待機。
#
# 実体はトップレベル state.py（状態＝Petri網のプレース）にあり、
# server.py と state_node が共有する同一シングルトン。

from state import set_token, get_token_state, reset_tokens, get_required_keys


# ─── LangGraph Designer Storage ─────────────────────────────────
import os
import shutil

GRAPHS_DIR = os.path.join(APP_ROOT, "data", "designer_graphs")
os.makedirs(GRAPHS_DIR, exist_ok=True)

_graph_lock = threading.Lock()


def _save_graph(name: str, data: dict) -> str:
    """Save a graph JSON file. Returns filename."""
    filename = f"{slugify(name)}.json"
    filepath = os.path.join(GRAPHS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filename


def _load_graph(filename: str) -> dict | None:
    """Load a graph JSON file."""
    filepath = os.path.join(GRAPHS_DIR, filename)
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_graphs() -> list:
    """List all saved graph filenames with metadata."""
    results = []
    for fname in sorted(os.listdir(GRAPHS_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(GRAPHS_DIR, fname)
        stat = os.stat(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Support both old (nodes/edges) and new (stages) formats
            node_count = len(data.get("nodes", [])) or len(data.get("stages", []))
            edge_count = len(data.get("edges", []))
            name = data.get("_meta", {}).get("name", fname.replace(".json", ""))
        except Exception:
            name = fname.replace(".json", "")
            node_count = "?"
            edge_count = "?"
        results.append({
            "filename": fname,
            "name": name,
            "nodeCount": node_count,
            "edgeCount": edge_count,
            "size": stat.st_size,
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return results


def _delete_graph(filename: str) -> bool:
    """Delete a graph file."""
    filepath = os.path.join(GRAPHS_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def slugify(s: str) -> str:
    """Simple slugify: lowercase, alphanumeric + hyphens + unicode."""
    import re
    s = re.sub(r"[^\w\-]", "-", s).lower()
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


# ─── Handlers ───────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        # Allow same-origin fetch from any origin (dev/local network)
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ──────────────────────────────────────────────────────

    def do_GET(self):
        if self.path == "/api/pesticides":
            conn = get_db()
            rows = conn.execute("SELECT * FROM pesticides ORDER BY id").fetchall()
            conn.close()
            result = []
            for r in rows:
                d = dict(r)
                # Parse JSON-string columns back to arrays
                for col in ("targetVector", "targetNames", "mixingBanTargets"):
                    if d.get(col) and isinstance(d[col], str):
                        try:
                            d[col] = json.loads(d[col])
                        except (json.JSONDecodeError, TypeError):
                            pass
                result.append(d)
            self._send_json(200, {"pesticides": result})
            return

        if self.path.startswith("/api/pesticides/"):
            drug_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM pesticides WHERE id=?", (drug_id,)).fetchone()
            conn.close()
            if row:
                d = dict(row)
                for col in ("targetVector", "targetNames", "mixingBanTargets"):
                    if d.get(col) and isinstance(d[col], str):
                        try:
                            d[col] = json.loads(d[col])
                        except (json.JSONDecodeError, TypeError):
                            pass
                self._send_json(200, d)
            else:
                self._send_json(404, {"error": f"pesticide {drug_id} not found"})
            return

        if self.path == "/api/diseases":
            conn = get_db()
            rows = conn.execute("SELECT * FROM diseases ORDER BY id").fetchall()
            conn.close()
            self._send_json(200, {"diseases": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/diseases/"):
            disease_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute("SELECT * FROM diseases WHERE id=?", (int(disease_id),)).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"disease {disease_id} not found"})
            return

        if self.path == "/api/eval-boxes/custom":
            conn = get_db()
            rows = conn.execute("SELECT * FROM eval_boxes_custom ORDER BY id").fetchall()
            conn.close()
            result = {}
            for r in rows:
                result[r["id"]] = {"name": r["name"], "vector": json.loads(r["vector"])}
            self._send_json(200, result)
            return

        if self.path == "/api/spray_history":
            conn = get_db()
            rows = conn.execute("SELECT * FROM spray_history ORDER BY date").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
            return

        if self.path == "/api/spray_schedule" or self.path.startswith("/api/spray_schedule?"):
            year = None
            if "?" in self.path:
                for kv in self.path.split("?", 1)[1].split("&"):
                    if kv.startswith("year="):
                        year = kv.split("=", 1)[1]
            conn = get_db()
            if year:
                rows = conn.execute(
                    "SELECT * FROM spray_schedule WHERE schedule_date LIKE ? ORDER BY schedule_date",
                    (f"{year}-%",),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM spray_schedule ORDER BY schedule_date").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/inventory"):
            self._handle_inventory_get()
            return

        if self.path == "/chat" or self.path == "/chat/":
            self._serve_chat_page()
            return

        if self.path == "/designer" or self.path == "/designer/":
            self._serve_designer_page()
            return

        if self.path == "/api/tokens/check":
            self._handle_tokens_check()
            return

        # Non-API: serve static files via parent class
        super().do_GET()

    # ── PUT ──────────────────────────────────────────────────────

    def do_PUT(self):
        if self.path.startswith("/api/pesticides/"):
            drug_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM pesticides WHERE id=?", (drug_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"pesticide {drug_id} not found"})
                return

            # Merge: keep existing fields, overwrite provided ones
            merged = dict(row)
            merged.update(body)
            merged["id"] = drug_id

            conn.execute(
                """UPDATE pesticides SET name=?, activeIngredient=?, category=?,
                   targetVector=?, targetNames=?, phiDays=?, mixingRestriction=?,
                   mixingBanTargets=?, maxApplications=?, toxicityClass=?,
                   system=?, systemCode=?, dilutionRate=?
                   WHERE id=?""",
                (
                    merged["name"],
                    merged.get("activeIngredient"),
                    merged.get("category"),
                    json.dumps(merged.get("targetVector", [])),
                    json.dumps(merged.get("targetNames", [])),
                    merged.get("phiDays"),
                    merged.get("mixingRestriction"),
                    json.dumps(merged.get("mixingBanTargets", [])),
                    merged.get("maxApplications"),
                    merged.get("toxicityClass"),
                    merged.get("system"),
                    merged.get("systemCode"),
                    merged.get("dilutionRate"),
                    drug_id,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": drug_id})
            return

        if self.path.startswith("/api/diseases/"):
            disease_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM diseases WHERE id=?", (int(disease_id),)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"disease {disease_id} not found"})
                return

            merged = dict(row)
            merged.update(body)
            merged["id"] = int(disease_id)

            conn.execute(
                "UPDATE diseases SET name=?, type=? WHERE id=?",
                (merged["name"], merged["type"], merged["id"]),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": disease_id})
            return

        if self.path.startswith("/api/spray_schedule/"):
            sched_id = self.path.split("/")[-1]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM spray_schedule WHERE id=?", (sched_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"spray_schedule {sched_id} not found"})
                return

            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                """UPDATE spray_schedule SET
                   schedule_date=?, actual_date=?, status=?, trigger_type=?, trigger_ref=?,
                   eval_box_id=?, rb_out_json=?, set_ids=?, pesticide_ids=?, operator=?,
                   weather=?, notes=?, updated_at=?
                   WHERE id=?""",
                (
                    body.get("schedule_date", row["schedule_date"]),
                    body.get("actual_date", row["actual_date"]),
                    body.get("status", row["status"]),
                    body.get("trigger_type", row["trigger_type"]),
                    body.get("trigger_ref", row["trigger_ref"]),
                    body.get("eval_box_id", row["eval_box_id"]),
                    json.dumps(body["rb_out_json"]) if body.get("rb_out_json") is not None else (row["rb_out_json"] if "rb_out_json" not in body else None),
                    json.dumps(body["set_ids"]) if "set_ids" in body else row["set_ids"],
                    json.dumps(body["pesticide_ids"]) if "pesticide_ids" in body else row["pesticide_ids"],
                    body.get("operator", row["operator"]),
                    body.get("weather", row["weather"]),
                    body.get("notes", row["notes"]),
                    now,
                    sched_id,
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "updated", "id": sched_id})
            return

        if self.path.startswith("/api/inventory/"):
            self._handle_inventory_put()
            return

        self._send_json(404, {"error": "not found"})
        return

    # ── DELETE ───────────────────────────────────────────────────

    def do_DELETE(self):
        if self.path.startswith("/api/pesticides/"):
            drug_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM pesticides WHERE id=?", (drug_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"pesticide {drug_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": drug_id})
            return

        if self.path.startswith("/api/diseases/"):
            disease_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM diseases WHERE id=?", (int(disease_id),))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"disease {disease_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": disease_id})
            return

        if self.path.startswith("/api/spray_history?date="):
            date = self.path.split("?date=")[1]
            conn = get_db()
            cur = conn.execute("DELETE FROM spray_history WHERE date=?", (date,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"record {date} not found"})
                return
            self._send_json(200, {"status": "deleted", "date": date})
            return

        if self.path.startswith("/api/spray_schedule/"):
            sched_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM spray_schedule WHERE id=?", (sched_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"spray_schedule {sched_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": sched_id})
            return

        if self.path.startswith("/api/inventory/"):
            inv_id = self.path.split("/")[-1]
            conn = get_db()
            cur = conn.execute("DELETE FROM inventory WHERE id=?", (inv_id,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
                return
            self._send_json(200, {"status": "deleted", "id": inv_id})
            return

        self._send_json(404, {"error": "not found"})
        return

    # ── POST ─────────────────────────────────────────────────────

    def do_POST(self):
        if self.path == "/api/pesticides":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            existing = conn.execute("SELECT id FROM pesticides WHERE id=?", (body.get("id"),)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f'pesticide {body.get("id")} already exists'})
                return

            required = ["id", "name", "activeIngredient", "category", "targetVector", "targetNames"]
            missing = [f for f in required if f not in body]
            if missing:
                conn.close()
                self._send_json(400, {"error": f"missing fields: {missing}"})
                return
            if not isinstance(body["targetVector"], list) or len(body["targetVector"]) != VECTOR_DIM:
                conn.close()
                self._send_json(400, {"error": f"targetVector must be {VECTOR_DIM}-length array"})
            if not isinstance(body["targetNames"], list):
                conn.close()
                self._send_json(400, {"error": "targetNames must be an array"})

            conn.execute(
                """INSERT INTO pesticides
                   (id, name, activeIngredient, category, targetVector, targetNames,
                    phiDays, mixingRestriction, mixingBanTargets, maxApplications,
                    toxicityClass, system, systemCode, dilutionRate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    body["id"],
                    body["name"],
                    body.get("activeIngredient"),
                    body.get("category"),
                    json.dumps(body["targetVector"]),
                    json.dumps(body["targetNames"]),
                    body.get("phiDays"),
                    body.get("mixingRestriction"),
                    json.dumps(body.get("mixingBanTargets", [])),
                    body.get("maxApplications"),
                    body.get("toxicityClass"),
                    body.get("system"),
                    body.get("systemCode"),
                    body.get("dilutionRate"),
                ),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": body["id"]})
            return

        if self.path == "/api/diseases":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            conn = get_db()
            existing = conn.execute("SELECT id FROM diseases WHERE id=?", (int(body.get("id")),)).fetchone()
            if existing:
                conn.close()
                self._send_json(409, {"error": f'disease {body.get("id")} already exists'})
                return

            required = ["id", "name", "type"]
            missing = [f for f in required if f not in body]
            if missing:
                conn.close()
                self._send_json(400, {"error": f"missing fields: {missing}"})
                return
            if body["type"] not in ("disease", "pest"):
                conn.close()
                self._send_json(400, {"error": 'type must be "disease" or "pest"'})

            conn.execute(
                "INSERT INTO diseases (id, name, type, icon) VALUES (?, ?, ?, ?)",
                (int(body["id"]), body["name"], body["type"], body.get("icon")),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": body["id"]})
            return

        if self.path == "/api/spray_history":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            date = body.get("date")
            pests = body.get("pests")
            vector = body.get("vector")

            if not date or not pests or vector is None:
                self._send_json(400, {"error": "missing fields: date, pests, vector"})
                return

            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO spray_history (date, pests, vector) VALUES (?, ?, ?)",
                (date, json.dumps(pests), json.dumps(vector)),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "date": date})
            return

        if self.path == "/api/spray_schedule/copy-year":
            self._handle_spray_schedule_copy_year()
            return

        # POST /api/spray_schedule/<id>/generate — 1行分の処方生成を即時実行
        # (UIの「⚡今すぐ」ボタン / cron を待たずRBP実行+DB更新+Slack通知)
        m = re.match(r"^/api/spray_schedule/(\d+)/generate$", self.path)
        if m:
            self._handle_spray_schedule_generate(m.group(1))
            return

        if self.path == "/api/spray_schedule":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            schedule_date = body.get("schedule_date")
            if not schedule_date:
                self._send_json(400, {"error": "missing field: schedule_date"})
                return

            now = datetime.datetime.utcnow().isoformat()
            conn = get_db()
            cur = conn.execute(
                """INSERT INTO spray_schedule
                   (schedule_date, actual_date, status, trigger_type, trigger_ref,
                    eval_box_id, rb_out_json, set_ids, pesticide_ids, operator,
                    weather, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule_date,
                    body.get("actual_date"),
                    body.get("status", "scheduled"),
                    body.get("trigger_type", "cycle"),
                    body.get("trigger_ref"),
                    body.get("eval_box_id"),
                    json.dumps(body.get("rb_out_json")) if body.get("rb_out_json") is not None else None,
                    json.dumps(body.get("set_ids", [])),
                    json.dumps(body.get("pesticide_ids", [])),
                    body.get("operator"),
                    body.get("weather"),
                    body.get("notes"),
                    now,
                    now,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            self._send_json(200, {"status": "created", "id": new_id})
            return

        if self.path.startswith("/api/inventory"):
            self._handle_inventory_post()
            return

        if self.path not in ("/api/eval-boxes", "/api/prescribe", "/api/chat/message", "/api/chat-webhook", "/api/designer/save", "/api/designer/list", "/api/designer/load", "/api/designer/delete", "/api/tokens/set", "/api/tokens/reset"):
            self._send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if self.path == "/api/chat/message":
            self._handle_chat_message(body)
            return

        if self.path == "/api/prescribe":
            self._handle_prescribe(body)
            return

        if self.path == "/api/chat-webhook":
            self._handle_chat_webhook(body)
            return

        if self.path == "/api/designer/save":
            self._handle_designer_save(body)
            return

        if self.path == "/api/designer/list":
            self._handle_designer_list()
            return

        if self.path == "/api/designer/load":
            self._handle_designer_load(body)
            return

        if self.path == "/api/designer/delete":
            self._handle_designer_delete(body)
            return

        if self.path == "/api/tokens/set":
            self._handle_tokens_set(body)
            return

        if self.path == "/api/tokens/reset":
            self._handle_tokens_reset(body)
            return

        box_id = body.get("id")
        name = body.get("name")
        vector = body.get("vector")

        if not isinstance(box_id, str) or not box_id:
            self._send_json(400, {"error": "id must be a non-empty string"})
            return
        if not isinstance(name, str):
            self._send_json(400, {"error": "name must be a string"})
            return
        if (not isinstance(vector, list) or len(vector) != VECTOR_DIM
                or any(v not in (0, 1) for v in vector)):
            self._send_json(400, {"error": f"vector must be a {VECTOR_DIM}-length array of 0/1"})
            return

        conn = get_db()
        existing = conn.execute("SELECT id FROM eval_boxes_custom WHERE id=?", (box_id,)).fetchone()
        if existing:
            conn.close()
            self._send_json(409, {"error": f"id {box_id} already registered", "existing": {"id": existing["id"], "name": existing["name"]}})
            return

        conn.execute(
            "INSERT INTO eval_boxes_custom (id, name, vector) VALUES (?, ?, ?)",
            (box_id, name, json.dumps(vector)),
        )
        conn.commit()
        conn.close()
        self._send_json(200, {"status": "OK", "id": box_id, "name": name})

    def _handle_prescribe(self, body):
        engine = body.get("engine")
        entry_vector = body.get("entryVector")

        if engine not in ("python", "haskell"):
            self._send_json(400, {"error": "engine must be 'python' or 'haskell'"})
            return
        if (not isinstance(entry_vector, list) or len(entry_vector) != VECTOR_DIM
                or any(v not in (0, 1) for v in entry_vector)):
            self._send_json(400, {"error": f"entryVector must be a {VECTOR_DIM}-length array of 0/1"})
            return

        try:
            if engine == "python":
                result = run_python_engine(entry_vector)
            else:
                result = run_haskell_engine(entry_vector)
        except Exception as e:
            self._send_json(500, {"error": f"{engine} engine error: {e}"})
            return

        status = 500 if isinstance(result, dict) and result.get("error") else 200
        self._send_json(status, result)

    # ── Chat AI ──────────────────────────────────────────────────

    def _serve_chat_page(self):
        """Serve the chat UI page."""
        chat_path = os.path.join(APP_ROOT, "chat.html")
        try:
            with open(chat_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "chat.html not found"})

    def _serve_designer_page(self):
        """Serve the LangGraph Designer page."""
        designer_path = os.path.join(APP_ROOT, "langgraph_designer.html")
        try:
            with open(designer_path, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json(404, {"error": "langgraph_designer.html not found"})

    def _handle_chat_message(self, body):
        """Handle chat message → Claude API."""
        message = body.get("message", "").strip()
        if not message:
            self._send_json(400, {"error": "message is required"})
            return

        try:
            from agentic_chat import run as agentic_run
            # Use client IP as thread_id for LangGraph conversation history
            thread_id = self.client_address[0] if self.client_address else "default"
            response = agentic_run(
                message,
                thread_id=thread_id,
            )
            self._send_json(200, {"response": response})
        except ImportError as e:
            self._send_json(503, {
                "response": f"⛔ agentic_chat モジュールが読み込めません。\n\n詳細: {str(e)}"
            })
        except Exception as e:
            self._send_json(500, {"error": f"チャットエラー: {str(e)[:200]}"})

    def _handle_chat_webhook(self, body):
        """Handle Slack webhook message send request."""
        text = body.get("text", "").strip()
        title = body.get("title", "").strip()
        sections = body.get("sections", [])

        if not text and not title:
            self._send_json(400, {"error": "text or title is required"})
            return

        if not text:
            text = title

        try:
            import sos
            if sections:
                result = sos.slack.send_card(title or text, sections)
            else:
                result = sos.slack.send_message(text)

            if result.get("success"):
                self._send_json(200, {"status": "sent"})
            else:
                self._send_json(500, {"error": result.get("error", "不明なエラー")})
        except ImportError:
            self._send_json(503, {"error": "sos モジュールが見つかりません"})
        except Exception as e:
            self._send_json(500, {"error": f"送信中にエラーが発生しました: {str(e)[:200]}"})

    # ─── LangGraph Designer API ───────────────────────────────────

    def _handle_designer_save(self, body):
        """Save a graph to disk."""
        name = body.get("name", "").strip()
        data = body.get("data")
        if not name or not data:
            self._send_json(400, {"error": "name and data are required"})
            return
        try:
            with _graph_lock:
                filename = _save_graph(name, data)
            self._send_json(200, {"ok": True, "filename": filename})
        except Exception as e:
            self._send_json(500, {"error": f"保存中にエラー: {str(e)[:200]}"})

    def _handle_designer_list(self):
        """List all saved graphs."""
        try:
            with _graph_lock:
                graphs = _list_graphs()
            self._send_json(200, {"graphs": graphs})
        except Exception as e:
            self._send_json(500, {"error": f"一覧取得中にエラー: {str(e)[:200]}"})

    def _handle_designer_load(self, body):
        """Load a specific graph."""
        filename = body.get("filename", "").strip()
        if not filename:
            self._send_json(400, {"error": "filename is required"})
            return
        try:
            with _graph_lock:
                data = _load_graph(filename)
            if data is None:
                self._send_json(404, {"error": "graph not found"})
                return
            self._send_json(200, {"data": data})
        except Exception as e:
            self._send_json(500, {"error": f"読取中にエラー: {str(e)[:200]}"})

    def _handle_designer_delete(self, body):
        """Delete a saved graph."""
        filename = body.get("filename", "").strip()
        if not filename:
            self._send_json(400, {"error": "filename is required"})
            return
        try:
            with _graph_lock:
                ok = _delete_graph(filename)
            if not ok:
                self._send_json(404, {"error": "graph not found"})
                return
            self._send_json(200, {"ok": True})
        except Exception as e:
            self._send_json(500, {"error": f"削除中にエラー: {str(e)[:200]}"})

    # ─── Token Management API ─────────────────────────────────────

    def _handle_tokens_check(self):
        """GET /api/tokens/check — Current token state."""
        self._send_json(200, get_token_state())

    def _handle_tokens_set(self, body):
        """POST /api/tokens/set — Set a single token."""
        key = body.get("key", "").strip()
        value = body.get("value", "").strip()
        valid_keys = sorted(get_required_keys())
        if not key or key not in valid_keys:
            self._send_json(400, {"error": f"key must be one of: {valid_keys}"})
            return
        if not value:
            self._send_json(400, {"error": "value is required"})
            return
        result = set_token(key, value)
        if "error" in result:
            self._send_json(400, result)
            return
        self._send_json(200, result)

    def _handle_tokens_reset(self, body):
        """POST /api/tokens/reset — Reset all tokens."""
        result = reset_tokens()
        self._send_json(200, result)

    # ─── Inventory Management API ─────────────────────────────────

    def _handle_inventory_get(self):
        """GET /api/inventory — List all inventory items.
           GET /api/inventory/<id> — Get single item.
           GET /api/inventory/by-pesticide/<pid> — List by pesticide ID.
        """
        if self.path == "/api/inventory":
            conn = get_db()
            rows = conn.execute(
                """SELECT i.*, p.name AS pesticideName, p.category
                   FROM inventory i
                   LEFT JOIN pesticides p ON i.pesticideId = p.id
                   ORDER BY i.expiryDate ASC, i.createdAt DESC"""
            ).fetchall()
            conn.close()
            self._send_json(200, {"inventory": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/inventory/by-pesticide/"):
            pid = self.path.split("/")[-1]
            conn = get_db()
            rows = conn.execute(
                """SELECT i.*, p.name AS pesticideName, p.category
                   FROM inventory i
                   LEFT JOIN pesticides p ON i.pesticideId = p.id
                   WHERE i.pesticideId = ?
                   ORDER BY i.expiryDate ASC""",
                (pid,)
            ).fetchall()
            conn.close()
            self._send_json(200, {"inventory": [dict(r) for r in rows]})
            return

        if self.path.startswith("/api/inventory/"):
            inv_id = self.path.split("/")[-1]
            conn = get_db()
            row = conn.execute(
                """SELECT i.*, p.name AS pesticideName, p.category
                   FROM inventory i
                   LEFT JOIN pesticides p ON i.pesticideId = p.id
                   WHERE i.id = ?""",
                (inv_id,)
            ).fetchone()
            conn.close()
            if row:
                self._send_json(200, dict(row))
            else:
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
            return

        self._send_json(404, {"error": "not found"})
        return

    def _handle_inventory_post(self):
        """POST /api/inventory — Create inventory item.
           POST /api/inventory/<id>/consume — Decrease quantity.
           POST /api/inventory/<id>/restock — Increase quantity.
        """
        # Special actions: consume / restock
        if self.path.endswith("/consume"):
            inv_id = self.path.split("/")[-2]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            amount = body.get("amount", 0)
            if amount <= 0:
                self._send_json(400, {"error": "amount must be positive"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
                return

            current_qty = row["quantity"]
            if amount > current_qty:
                conn.close()
                self._send_json(400, {
                    "error": f"在庫が不足しています。現在の在庫: {current_qty}",
                    "available": current_qty,
                    "requested": amount,
                })
                return

            new_qty = current_qty - amount
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE inventory SET quantity=?, updatedAt=? WHERE id=?",
                (new_qty, now, inv_id)
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "consumed", "id": inv_id, "remaining": new_qty})
            return

        if self.path.endswith("/restock"):
            inv_id = self.path.split("/")[-2]
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON body"})
                return

            amount = body.get("amount", 0)
            if amount <= 0:
                self._send_json(400, {"error": "amount must be positive"})
                return

            conn = get_db()
            row = conn.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"inventory {inv_id} not found"})
                return

            new_qty = row["quantity"] + amount
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE inventory SET quantity=?, updatedAt=? WHERE id=?",
                (new_qty, now, inv_id)
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "restocked", "id": inv_id, "total": new_qty})
            return

        # Normal: create new inventory item
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        pesticide_id = body.get("pesticideId")
        product_name = body.get("productName")
        quantity = body.get("quantity", 0)

        if not pesticide_id or not product_name:
            self._send_json(400, {"error": "pesticideId and productName are required"})
            return

        # Verify pesticide exists
        conn = get_db()
        pest_row = conn.execute("SELECT id FROM pesticides WHERE id=?", (pesticide_id,)).fetchone()
        if pest_row is None:
            conn.close()
            self._send_json(404, {"error": f"pesticide {pesticide_id} not found"})
            return

        now = datetime.datetime.utcnow().isoformat()
        inv_id = body.get("id", f"INV-{len(conn.execute('SELECT id FROM inventory').fetchall()):04d}")

        conn.execute(
            """INSERT INTO inventory
               (id, pesticideId, productName, lotNumber, quantity, unit,
                expiryDate, supplier, purchaseDate, notes, createdAt, updatedAt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inv_id,
                pesticide_id,
                product_name,
                body.get("lotNumber"),
                float(quantity),
                body.get("unit", "ml"),
                body.get("expiryDate"),
                body.get("supplier"),
                body.get("purchaseDate"),
                body.get("notes"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        self._send_json(200, {"status": "created", "id": inv_id})
        return

    def _handle_inventory_put(self):
        """PUT /api/inventory/<id> — Update inventory item."""
        inv_id = self.path.split("/")[-1]
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        conn = get_db()
        row = conn.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone()
        if row is None:
            conn.close()
            self._send_json(404, {"error": f"inventory {inv_id} not found"})
            return

        # Validate pesticideId if provided
        if "pesticideId" in body:
            pest_row = conn.execute(
                "SELECT id FROM pesticides WHERE id=?", (body["pesticideId"],)
            ).fetchone()
            if pest_row is None:
                conn.close()
                self._send_json(404, {"error": f"pesticide {body['pesticideId']} not found"})
                return

        now = datetime.datetime.utcnow().isoformat()
        conn.execute(
            """UPDATE inventory SET
               pesticideId=?, productName=?, lotNumber=?, quantity=?, unit=?,
               expiryDate=?, supplier=?, purchaseDate=?, notes=?, updatedAt=?
               WHERE id=?""",
            (
                body.get("pesticideId", row["pesticideId"]),
                body.get("productName", row["productName"]),
                body.get("lotNumber", row["lotNumber"]),
                float(body.get("quantity", row["quantity"])),
                body.get("unit", row["unit"]),
                body.get("expiryDate", row["expiryDate"]),
                body.get("supplier", row["supplier"]),
                body.get("purchaseDate", row["purchaseDate"]),
                body.get("notes", row["notes"]),
                now,
                inv_id,
            ),
        )
        conn.commit()
        conn.close()
        self._send_json(200, {"status": "updated", "id": inv_id})
        return

    def _handle_spray_schedule_generate(self, sched_id):
        """POST /api/spray_schedule/<id>/generate — 指定1行の処方生成を即時実行。

        UI の「⚡今すぐ」ボタン用。scripts/rx_prescribe.py の process_row を再利用し、
        RBP 実行 → spray_schedule 更新 → Slack 通知 を行う。
        """
        try:
            sys.path.insert(0, os.path.join(APP_ROOT, "scripts"))
            import rx_prescribe
            from datetime import datetime, timedelta, timezone
            jst = timezone(timedelta(hours=9))
            now = datetime.now(jst)

            conn = get_db()
            row = conn.execute(
                "SELECT * FROM spray_schedule WHERE id=?", (sched_id,)
            ).fetchone()
            if row is None:
                conn.close()
                self._send_json(404, {"error": f"spray_schedule {sched_id} not found"})
                return

            result = rx_prescribe.process_row(
                row, now,
                rx_prescribe.load_eval_box_vectors(),
                rx_prescribe.load_pesticide_meta(),
                conn,
            )
            conn.close()
            if not result["ok"]:
                self._send_json(422, {"error": result["error"]})
                return
            self._send_json(200, {
                "status": "generated",
                "id": sched_id,
                "set_label": result["set_label"],
                "pesticides": result["names"],
                "slack": "ok" if result["slack_ok"] else "failed",
            })
        except Exception as e:
            self._send_json(500, {"error": f"generate error: {e}"})

    def _handle_spray_schedule_copy_year(self):
        """POST /api/spray_schedule/copy-year — 年度複製。
           spray_schedule に fromYear のデータがあればそれを複製元にする
           （日付の年だけ置き換え、status='scheduled' にリセット）。
           なければ spray_history の fromYear データをブートストラップ元として使う
           （set_ids/pesticide_ids は空配列で作成し、手動入力を促す）。
           toYear に既存の同日エントリがある場合はスキップする。
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        from_year = body.get("fromYear")
        to_year = body.get("toYear")
        if not from_year or not to_year:
            self._send_json(400, {"error": "fromYear and toYear are required"})
            return
        from_year = str(from_year)
        to_year = str(to_year)

        conn = get_db()
        existing_target_dates = {
            r["schedule_date"] for r in conn.execute(
                "SELECT schedule_date FROM spray_schedule WHERE schedule_date LIKE ?",
                (f"{to_year}-%",),
            ).fetchall()
        }

        source_rows = conn.execute(
            "SELECT * FROM spray_schedule WHERE schedule_date LIKE ? ORDER BY schedule_date",
            (f"{from_year}-%",),
        ).fetchall()

        now = datetime.datetime.utcnow().isoformat()
        created = 0
        skipped = 0

        if source_rows:
            source = "spray_schedule"
            for r in source_rows:
                new_date = to_year + r["schedule_date"][4:]
                if new_date in existing_target_dates:
                    skipped += 1
                    continue
                conn.execute(
                    """INSERT INTO spray_schedule
                       (schedule_date, actual_date, status, trigger_type, trigger_ref,
                        eval_box_id, rb_out_json, set_ids, pesticide_ids, operator,
                        weather, notes, created_at, updated_at)
                       VALUES (?, NULL, 'scheduled', ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)""",
                    (
                        new_date,
                        r["trigger_type"],
                        str(r["id"]),
                        r["eval_box_id"],
                        r["rb_out_json"],
                        r["set_ids"],
                        r["pesticide_ids"],
                        r["notes"],
                        now,
                        now,
                    ),
                )
                created += 1
        else:
            source = "spray_history"
            history_rows = conn.execute(
                "SELECT * FROM spray_history WHERE date LIKE ? ORDER BY date",
                (f"{from_year}-%",),
            ).fetchall()
            for r in history_rows:
                new_date = to_year + r["date"][4:]
                if new_date in existing_target_dates:
                    skipped += 1
                    continue
                pests = json.loads(r["pests"]) if r["pests"] else []
                conn.execute(
                    """INSERT INTO spray_schedule
                       (schedule_date, actual_date, status, trigger_type, trigger_ref,
                        eval_box_id, rb_out_json, set_ids, pesticide_ids, operator,
                        weather, notes, created_at, updated_at)
                       VALUES (?, NULL, 'scheduled', 'cycle', ?, NULL, NULL, '[]', '[]', NULL, NULL, ?, ?, ?)""",
                    (
                        new_date,
                        r["date"],
                        "、".join(pests) if pests else None,
                        now,
                        now,
                    ),
                )
                created += 1

        conn.commit()
        conn.close()
        self._send_json(200, {
            "status": "OK",
            "source": source,
            "fromYear": from_year,
            "toYear": to_year,
            "created": created,
            "skipped": skipped,
        })
        return


def main():
    init_db()
    # Listen backlog increased to 128 (class attr) to prevent browser connect stalls
    server = ThreadingHTTPServer(("0.0.0.0", 9999), Handler)
    print(f"Serving on 0.0.0.0:9999 — DB: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
