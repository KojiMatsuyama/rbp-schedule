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
import json
import os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_ROOT)

CUSTOM_EVAL_BOXES_PATH = os.path.join(APP_ROOT, 'data', 'eval_boxes_custom.json')
VECTOR_DIM = 10


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
        if self.path != '/api/eval-boxes':
            self._send_json(404, {'error': 'not found'})
            return

        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length > 0 else b''
            body = json.loads(raw.decode('utf-8')) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {'error': 'invalid JSON body'})
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


def main():
    server = ThreadingHTTPServer(('0.0.0.0', 9999), Handler)
    server.serve_forever()


if __name__ == '__main__':
    main()
