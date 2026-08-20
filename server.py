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
import re
import sqlite3
import subprocess
import sys
import threading
import datetime
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


# ─── Conversation History Store ─────────────────────────────────
# Thread-local storage for per-client conversation history
import threading

_conv_lock = threading.Lock()
_conversations = {}  # client_id -> [(role, content), ...]


def _get_conversation(client_id: str) -> list:
    with _conv_lock:
        return _conversations.setdefault(client_id, [])


def _append_conversation(client_id: str, role: str, content: str):
    with _conv_lock:
        conv = _conversations.setdefault(client_id, [])
        conv.append((role, content))
        # Keep last 20 messages
        if len(conv) > 20:
            _conversations[client_id] = conv[-20:]


# ─── LangGraph Token Store (Petri net model) ─────────────────────
# トークン集約ノードの状態をメモリ上に保持。
# クライアント（スケジュールタイマー / カレンダーUI）がトークンを投入。
# 全トークンが揃うまでエージェントは待機。
#
# 実体は agentic_chat/tokens.py にあり、server.py と nodes.py が共有。

from agentic_chat.tokens import set_token, get_token_state, reset_tokens, get_required_keys


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

        if self.path == "/api/records":
            conn = get_db()
            rows = conn.execute("SELECT * FROM records ORDER BY date").fetchall()
            conn.close()
            self._send_json(200, {"success": True, "data": [dict(r) for r in rows]})
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
                "INSERT INTO diseases (id, name, type, icon) VALUES (?, ?, ?, ?)",
                (int(body["id"]), body["name"], body["type"], body.get("icon")),
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

        # Detect Slack notification intent and inject conversation context
        slack_intent_keywords = [
            # Japanese
            "slackに通知", "slackに送信", "slackに送って", "slackに投げて",
            "メンバーに通知", "メンバーに共有", "チームに共有", "Slackで共有",
            "通知して", "共有して",
            # English
            "slack", "notify", "notification", "share",
            "member", "team",
        ]
        is_slack_request = any(kw in message.lower() for kw in slack_intent_keywords)

        try:
            from agentic_chat import run as agentic_run
            # Use client IP as thread_id for LangGraph conversation history
            thread_id = self.client_address[0] if self.client_address else "default"
            response = agentic_run(
                message,
                is_slack_request=is_slack_request,
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
            from chat_client import send_message, send_message_with_card
            if sections:
                result = send_message_with_card(title or text, sections)
            else:
                result = send_message(text)

            if result.get("success"):
                self._send_json(200, {"status": "sent"})
            else:
                self._send_json(500, {"error": result.get("error", "不明なエラー")})
        except ImportError:
            self._send_json(503, {"error": "chat_client モジュールが見つかりません"})
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


def main():
    init_db()
    # Listen backlog increased to 128 (class attr) to prevent browser connect stalls
    server = ThreadingHTTPServer(("0.0.0.0", 9999), Handler)
    print(f"Serving on 0.0.0.0:9999 — DB: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
