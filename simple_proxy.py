#!/usr/bin/env python3
"""
simple_proxy.py — Anthropic API互換の簡易プロキシ

APIキー不要でAnthropic互換APIを提供する。
内部でLiteLLM（192.168.131.161:24200）にリクエストを転送する。
"""

import json
import urllib.request
import urllib.error
import ssl
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

LITELLM_BASE = "http://192.168.131.161:24200"
PORT = 24201


class ProxyHandler(BaseHTTPRequestHandler):
    def _log(self, msg):
        print(f"[proxy] {msg}", flush=True)

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return

        # Forward to LiteLLM
        url = f"{LITELLM_BASE}{self.path}"
        req = urllib.request.Request(url, data=raw, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                result = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)
        except urllib.error.HTTPError as e:
            error_body = e.read()
            self._log(f"LiteLLM error: {e.code} {e.reason}")
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)
        except Exception as e:
            self._log(f"Proxy error: {e}")
            self._send_json(502, {"error": f"Bad gateway: {str(e)[:200]}"})

    def log_message(self, format, *args):
        self._log(f"{self.address_string()} - {format % args}")


def main():
    server = HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"Simple Anthropic proxy running on 0.0.0.0:{PORT}")
    print(f"Forwarding to LiteLLM at {LITELLM_BASE}")
    server.serve_forever()


if __name__ == "__main__":
    main()
