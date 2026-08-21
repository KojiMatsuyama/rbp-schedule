#!/usr/bin/env python3
"""
agentic_chat/tokens.py — トークン管理（Petri netモデル）

トークン集約ノードとサーバーAPIが共有する状態ストア。
外部（スケジュールタイマー / カレンダーUI）がトークンを投入し、
全トークンが揃うとエージェントが発火する。

トークン一覧:
  schedule      — スケジュール（日時）
  crop          — 作物種
  environment   — 栽培環境
  growth_stage  — 生育段階
"""

import threading

_TOKEN_KEYS = frozenset(["schedule", "crop", "environment", "growth_stage"])
_tokens_lock = threading.Lock()
_tokens_store: dict[str, str | None] = {
    "schedule": None,
    "crop": None,
    "environment": None,
    "growth_stage": None,
}


def set_token(key: str, value: str) -> dict:
    """Set a single token. Returns current token state."""
    if key not in _TOKEN_KEYS:
        return {"error": f"unknown token key: {key}. valid keys: {sorted(_TOKEN_KEYS)}"}
    with _tokens_lock:
        _tokens_store[key] = value
        ready = all(v is not None for v in _tokens_store.values())
        return {
            "ok": True,
            "key": key,
            "value": value,
            "tokens": dict(_tokens_store),
            "ready": ready,
        }


def get_token_state() -> dict:
    """Get current token state."""
    with _tokens_lock:
        return {
            "tokens": dict(_tokens_store),
            "ready": all(v is not None for v in _tokens_store.values()),
        }


def reset_tokens() -> dict:
    """Reset all tokens."""
    with _tokens_lock:
        for k in _TOKEN_KEYS:
            _tokens_store[k] = None
        return {"ok": True, "tokens": dict(_tokens_store), "ready": False}


def get_required_keys() -> frozenset:
    """Return the set of required token keys."""
    return _TOKEN_KEYS
