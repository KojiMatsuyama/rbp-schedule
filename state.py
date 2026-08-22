#!/usr/bin/env python3
"""
state.py — 状態（プレース: トークン集約・発火判定）トップレベル独立モジュール

業務中心無人思想（Petri網）:
    プレース（状態: トークン1, トークン2…）
      → トランジション [ 認知 ─ 評価 ─ 決定 ─ 投射 ]
        → 作動（sos）

本モジュールは「状態」＝ Petri網の**プレース**（トークンを保持する箇所）を担当する。
トランジション群 [認知─評価─決定─投射] の**前段**であり、
スケジュール / カレンダー / API から投入されたトークンを集約し、
全トークンが揃うまで待機（checkpoint）、揃ったらエージェントを発火させる。

## トークンストア（プレースの内容）— 単一シングルトン
    set_token / get_token_state / reset_tokens / get_required_keys
- server.py の API（POST /api/tokens/set 等）と state_node が共有する**同一ストア**。
  スレッドセーフ（threading.Lock）。
- 書き込み（API）と読み出し（state_node）が必ずこの1つのモジュールを経由する
  ことで、同一の _tokens_store（シングルトン）を維持する。
  ※ かつて agentic_chat/tokens.py にあったが、server.py が agentic_chat を
    import すると LangGraph 一式を引くため、トップレベルへ移管した。
    移管先のモジュールが2つに分かれると双シングルトン化して壊れるため、
    agentic_chat/tokens.py は削除する。

## API
- state_node(state) — ① 状態ノード本体。トークン集約・発火判定（Petri netのプレース）

## 配置理由
- `agentic_chat/` 配下に置くと、server/cron から import すると
  `agentic_chat/__init__.py`（=LangGraph 一式）が引かれる。
  トップレベルなら `import state` のみで済む。
- ※ 本モジュールは agentic_chat/state.py（ChatState＝LangGraphのグローバル状態
  オブジェクト）とは別物。本モジュールは「プレース＝トークンストア＋発火判定」で、
  名前空間も異なる（トップレベル state vs agentic_chat.state）。
"""

import logging
import threading

logger = logging.getLogger(__name__)

# =====================================================================
# トークンストア（プレースの内容）
# =====================================================================
# トークン一覧（Petri netのプレースに投入されるトークン）:
#   schedule      — スケジュール（日時）
#   crop          — 作物種
#   environment   — 栽培環境
#   growth_stage  — 生育段階

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


# =====================================================================
# ① 状態ノード — トークン集約・発火判定（Petri netのプレース）
# =====================================================================

def state_node(state: dict) -> dict:
    """
    ① 状態ノード — トークン集約・発火判定（Petri netモデル）。

    本モジュール内のトークンストア（= Petri netのプレース）からトークンを
    読み取り、全トークンが揃うまで待機（checkpointで保持）。
    全トークンが揃ったらエージェントを発火させる。

    トークン入力源:
      - スケジュール: 設定した日時になるとイベント（防除トークン）が入力
      - カレンダー: 日付をクリックすると防除トークンが入力
      - API: POST /api/tokens/set で手動投入

    Returns:
        {
            "token_ready": "ready",         # "pending" | "ready"
            "schedule": "2026-08-20T09:00",
            "crop": "きゅうり",
            "environment": "温室",
            "growth_stage": "育苗中",
        }
    """
    token_state = get_token_state()
    tokens = token_state["tokens"]

    # 全トークンが揃っているかチェック
    all_present = token_state["ready"]

    if not all_present:
        # 未完了 → checkpointに保存して待機
        missing = [k for k in ["schedule", "crop", "environment", "growth_stage"]
                   if tokens[k] is None]
        logger.info(f"[状態] トークン不足で待機中: {missing}")
        return {
            "token_ready": "pending",
            "schedule": tokens["schedule"],
            "crop": tokens["crop"],
            "environment": tokens["environment"],
            "growth_stage": tokens["growth_stage"],
        }

    # 全トークン揃った → 発火
    logger.info("[状態] 全トークン揃った。エージェント発火！")
    return {
        "token_ready": "ready",
        "schedule": tokens["schedule"],
        "crop": tokens["crop"],
        "environment": tokens["environment"],
        "growth_stage": tokens["growth_stage"],
    }
