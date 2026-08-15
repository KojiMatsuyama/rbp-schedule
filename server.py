#!/usr/bin/env python3
# server.py — STBアプリ用の静的ファイル配信 + EVAL_BOX自動登録の永続化サーバー。
# 起動.txt が記載していたヒアドキュメント生成のThreadingHTTPServerと同じ挙動
# （IP/ポート/バインド/キャッシュ無効化/ThreadingHTTPServer必須）を維持しつつ、
# POST /api/eval-boxes のみを追加で処理する。
#
# 要求評価RBP（rbp/eval_box_registry.js）が自動登録した新しいEVAL_BOXを
# 全ユーザー・全端末で共有するため、data/eval_boxes_custom.jsonへ永続化する。
# ID・名称・ベクトルの計算ロジックはすべてJS側（rbp/eval_box_registry.js）が単一の情報源で、
# このエンドポイントは検証つきの「追記のみ」を行う（薬剤側=仕様決定RBPは自動追加しない）。
import glob
import json
import os
import subprocess
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_ROOT)

CUSTOM_EVAL_BOXES_PATH = os.path.join(APP_ROOT, 'data', 'eval_boxes_custom.json')
VECTOR_DIM = 10

# --- POST /api/prescribe: RBPエンジン切替（python / haskell） ---
# フロントのラジオボタン（index.html）から entryVector を受け取り、
# サーバー側PoCエンジンで処方セットを計算して返す。
# どちらもサンプル13剤DB・簡略スコアのPoCで、JS版と結果が異なる場合がある。
PY_ENGINE_DIR = os.path.join(APP_ROOT, 'rbp-algebra-python')
_py_engine = None  # lazy-load cache


def run_python_engine(entry_vector):
    global _py_engine
    if _py_engine is None:
        sys.path.insert(0, PY_ENGINE_DIR)
        import api as _py_engine_mod
        _py_engine = _py_engine_mod
    return _py_engine.prescribe(entry_vector)


def find_haskell_bin():
    # dist-newstyle-user（一般ユーザーでのビルド先）を優先し、次にdist-newstyle。
    # 古いバイナリ（--prescribe未対応）を誤って掴まないよう、更新が新しい方を選ぶ。
    hits = []
    for build_root in ('dist-newstyle-user', 'dist-newstyle'):
        pattern = os.path.join(
            APP_ROOT, 'rbp-algebra', build_root, 'build', '*', '*',
            'rbp-algebra-*', 'x', 'rbp-algebra', 'build', 'rbp-algebra', 'rbp-algebra')
        hits.extend(glob.glob(pattern))
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def run_haskell_engine(entry_vector):
    bin_path = find_haskell_bin()
    if bin_path is None:
        return {'error': 'Haskellバイナリが見つかりません（rbp-algebra/ で cabal build が必要）'}
    csv = ','.join(str(v) for v in entry_vector)
    try:
        proc = subprocess.run(
            [bin_path, '--prescribe', csv],
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {'error': 'Haskellエンジンがタイムアウトしました'}
    if proc.returncode != 0:
        return {'error': f'Haskellエンジンが異常終了しました: {proc.stderr[:500]}'}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {'error': f'HaskellエンジンのJSON出力を解析できません: {proc.stdout[:200]}'}


def load_custom_eval_boxes():
    if not os.path.exists(CUSTOM_EVAL_BOXES_PATH):
        return {}
    with open(CUSTOM_EVAL_BOXES_PATH, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def save_custom_eval_boxes(data):
    with open(CUSTOM_EVAL_BOXES_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # キャッシュ無効化（コード修正が即ブラウザに反映されるように）
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ('/api/eval-boxes', '/api/prescribe'):
            self._send_json(404, {'error': 'not found'})
            return

        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length > 0 else b''
            body = json.loads(raw.decode('utf-8')) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {'error': 'invalid JSON body'})
            return

        if self.path == '/api/prescribe':
            self._handle_prescribe(body)
            return

        box_id = body.get('id')
        name = body.get('name')
        vector = body.get('vector')

        if not isinstance(box_id, str) or not box_id:
            self._send_json(400, {'error': 'id must be a non-empty string'})
            return
        if not isinstance(name, str):
            self._send_json(400, {'error': 'name must be a string'})
            return
        if (not isinstance(vector, list) or len(vector) != VECTOR_DIM
                or any(v not in (0, 1) for v in vector)):
            self._send_json(400, {'error': f'vector must be a {VECTOR_DIM}-length array of 0/1'})
            return

        custom = load_custom_eval_boxes()
        if box_id in custom:
            self._send_json(409, {'error': f'id {box_id} already registered', 'existing': custom[box_id]})
            return

        custom[box_id] = {'name': name, 'vector': vector}
        save_custom_eval_boxes(custom)
        self._send_json(200, {'status': 'OK', 'id': box_id, 'name': name})

    def _handle_prescribe(self, body):
        engine = body.get('engine')
        entry_vector = body.get('entryVector')

        if engine not in ('python', 'haskell'):
            self._send_json(400, {'error': "engine must be 'python' or 'haskell'"})
            return
        if (not isinstance(entry_vector, list) or len(entry_vector) != VECTOR_DIM
                or any(v not in (0, 1) for v in entry_vector)):
            self._send_json(400, {'error': f'entryVector must be a {VECTOR_DIM}-length array of 0/1'})
            return

        try:
            if engine == 'python':
                result = run_python_engine(entry_vector)
            else:
                result = run_haskell_engine(entry_vector)
        except Exception as e:  # PoCエンジン内部の想定外エラーはサーバーを落とさず500で返す
            self._send_json(500, {'error': f'{engine} engine error: {e}'})
            return

        status = 500 if isinstance(result, dict) and result.get('error') else 200
        self._send_json(status, result)


def main():
    server = ThreadingHTTPServer(('0.0.0.0', 9999), Handler)
    server.serve_forever()


if __name__ == '__main__':
    main()
