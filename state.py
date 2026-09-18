#!/usr/bin/env python3
"""
state.py — 状態（プレース: トークン集約・発火判定）トップレベル独立モジュール

業務中心無人思想（Petri網）:
    プレース（状態: トークン1, トークン2…）
      → トランジション [ 認知 ─ 評価 ─ 決定 ─ 投射 ]
        → 作動（sos）

## 石1 以降: トークンストアは HUMOS（humos.py）へ昇格

本モジュールの「トークンストア（プレースの内容）」は、石1（HUMOS の 3 関数
インターフェース、ts/真界_実装状況.md）で **humos.py の `_marking`（マーキング
ストア）に昇格**した。本モジュールは humos への**純再エクスポート・シム**に
落ちる（双シングルトン化を避けるため dict ラップ・値コピーはしない）。

- `set_token / get_token_state / reset_tokens / get_required_keys` は
  `from humos import ...` の再エクスポート（同一シングルトン）。
- server.py の `from state import ...`（:272）は**変更不要**のまま動作
  （シム経由で humos に到達）。

## 本モジュールに残るもの
- `state_node(state)` — ① 状態ノード本体。トークン集約・発火判定
  （Petri net のプレース）。humos のマーキング（get_token_state）を読む。
  jobnet.NET の `"handler": "state.state_node"` 文字列（遅延解決）で参照される。

## 配置理由
- `agentic_chat/` 配下に置くと、server/cron から import すると
  `agentic_chat/__init__.py`（=jobnet→nodes 一式）が引かれる。
  トップレベルなら `import state` のみで済む。
- ※ 本モジュールは agentic_chat/state.py（ChatState＝状態遷移ネット実行の状態dict）
  とは別物。本モジュールは「プレース＝トークンストア（シム）＋発火判定」で、
  名前空間も異なる（トップレベル state vs agentic_chat.state）。
"""

import logging

from humos import (  # noqa: F401  (純再エクスポート・シム — 同一シングルトン)
    set_token,
    get_token_state,
    reset_tokens,
    get_required_keys,
)

logger = logging.getLogger(__name__)


# =====================================================================
# ① 状態ノード — トークン集約・発火判定（Petri netのプレース）
# =====================================================================

def state_node(state: dict) -> dict:
    """
    ① 状態ノード — トークン集約・発火判定（Petri netモデル）。

    HUMOS のマーキング（= Petri netのプレース、humos.get_token_state）から
    トークンを読み取り、全トークンが揃うまで待機（checkpointで保持）。
    全トークンが揃ったら実働を発火させる。

    トークン入力源:
      - スケジュール: 設定した日時になるとイベント（防除トークン）が入力
      - カレンダー: 日付をクリックすると防除トークンが入力
      - API: POST /api/tokens/set で手動投入

    Returns:
        {
            "token_ready": "ready",            # "pending" | "ready"
            "field_type": "🌱 育苗 育苗圃（200m²）",
            "pest_matrix": "[1,0,0,1,0,0,0,0,1,0]",
        }
    """
    token_state = get_token_state()
    tokens = token_state["tokens"]

    # 全トークンが揃っているかチェック
    all_present = token_state["ready"]

    if not all_present:
        # 未完了 → checkpointに保存して待機
        missing = [k for k in ["field_type", "pest_matrix"] if tokens.get(k) is None]
        logger.info(f"[状態] トークン不足で待機中: {missing}")
        return {
            "token_ready": "pending",
            "field_type": tokens.get("field_type"),
            "pest_matrix": tokens.get("pest_matrix"),
        }

    # 全トークン揃った → 発火
    logger.info("[状態] 全トークン揃った。実働発火！")
    return {
        "token_ready": "ready",
        "field_type": tokens.get("field_type"),
        "pest_matrix": tokens.get("pest_matrix"),
    }
