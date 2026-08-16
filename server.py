#!/usr/bin/env python3
# server.py — STBアプリ用の静的ファイル配信 + SQLite永続化サーバー。
# IP/ポート/バインド/キャッシュ無効化/ThreadingHTTPServer必須
#
# SQLite (data/stb.db) に以下のテーブルを持つ:
#   pesticides, diseases, eval_boxes, eval_boxes_custom, records
#
# 要求評価RBP（rbp/eval_box_registry.js）が自動登録した新しいEVAL_BOXを
# eval_boxes_custom テーブルへ永続化する。
import glob
import json
import os
import sqlite3
import subprocess
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn


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
            systemCode TEXT
        );

        CREATE TABLE IF NOT EXISTS diseases (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('disease', 'pest'))
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

        CREATE TABLE IF NOT EXISTS records (
            date TEXT PRIMARY KEY,
            pests TEXT NOT NULL,
            vector TEXT NOT NULL
        );
    """)
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

        if self.path == "/api/records":
            conn = get_db()
            rows = conn.execute("SELECT * FROM records ORDER BY date").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
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
                   system=?, systemCode=?
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

        if self.path.startswith("/api/records?date="):
            date = self.path.split("?date=")[1]
            conn = get_db()
            cur = conn.execute("DELETE FROM records WHERE date=?", (date,))
            conn.commit()
            conn.close()
            if cur.rowcount == 0:
                self._send_json(404, {"error": f"record {date} not found"})
                return
            self._send_json(200, {"status": "deleted", "date": date})
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
                    toxicityClass, system, systemCode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                "INSERT INTO diseases (id, name, type) VALUES (?, ?, ?)",
                (int(body["id"]), body["name"], body["type"]),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "id": body["id"]})
            return

        if self.path == "/api/records":
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
                "INSERT OR REPLACE INTO records (date, pests, vector) VALUES (?, ?, ?)",
                (date, json.dumps(pests), json.dumps(vector)),
            )
            conn.commit()
            conn.close()
            self._send_json(200, {"status": "created", "date": date})
            return

        if self.path not in ("/api/eval-boxes", "/api/prescribe"):
            self._send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if self.path == "/api/prescribe":
            self._handle_prescribe(body)
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


def main():
    init_db()
    # Listen backlog increased to 128 (class attr) to prevent browser connect stalls
    server = ThreadingHTTPServer(("0.0.0.0", 9999), Handler)
    print(f"Serving on 0.0.0.0:9999 — DB: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
