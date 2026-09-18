#!/usr/bin/env python3
"""IF（膜）ファサード — 作動（起動）を IF を跨いで実働に届ける。

IF 設計（[ts/真界_IF設計.md](../ts/真界_IF設計.md) §0.1・§8）:
  投射（内容生成、T 内部）→ 作動（起動）──IF──▶ 実働（実行）

本モジュールは**作動（起動）の出口**＝IF ファサード。要求（投射の内容）を受け、
チャネル（実働を担う主体）を解決し、アダプタで届ける。

**名前の由来**: 設計書 §0「IF とは — 膜（membrane）」。`if` は Python の予約語
なので、IF=膜 として `membrane` と名付ける。

**挙動不変リファクタ（①）**: 現行はチャネルが Slack の1つだけなので、
`_resolve` は常に `SlackAdapter` を返す（既存 `sos.slack.send_message` への
委譲）。write_sos（作動ログ）は `sos.slack` 内で呼ばれる（本層では呼ばない）。

**②（自動チャネル解決）= 統合済み（C/D）**: `_resolve`/`deliver` が `conn` を
受け取ると、`capability.resolve_channel` に委譲し、アダプタの `requires`
（能力事実の要件）を entity_attribute（attr_kind=state）で照会して自動解決する
（§8.10）。`conn` なしは現行挙動（serves ∩ available_at、DB 不要）を維持。
記号化事実（導入済み/未導入等）から解決するため、人はルールでなく事実の
記号化をする（§8.1）。入方向（要求の STN への到達）は石5（`place_external_token`）
が前提。
"""

import logging

from .adapter import SlackAdapter

logger = logging.getLogger(__name__)

# 登録済み実働チャンネル（アダプタ）。②で増やす（AppAPIAdapter 等）。
_ADAPTERS = [SlackAdapter()]


def _resolve(requirement: dict, target: dict = None, conn=None):
    """チャネル解決（§8.10）。

    serves ∩ available_at のマッチング＋優先度（max）＋フォールバック。

    - **conn なし（現行・①）**: serves ∩ available_at で解決（DB 不要）。
      _ADAPTERS が Slack のみなので常に SlackAdapter を返す（挙動不変）。
    - **conn あり（②自動チャネル解決）**: `capability.resolve_channel` に委譲し、
      アダプタの `requires`（能力事実の要件）を entity_attribute（attr_kind=state）
      で照会する。記号化された事実（導入済み/未導入等）から自動で解決。
      available_at（カスタム判定）も併用（AND）。

    Args:
        requirement: 要求 dict（type = 要求の種）。
        target: 解決の文脈（entity の 3-level ID 等）。
        conn: DB 接続（humos.get_conn()）。与えれば能力事実（記号化事実）から解決。

    Returns:
        解決されたアダプタ。

    Raises:
        RuntimeError: アダプタが1つも登録されていない場合。
    """
    if conn is not None:
        import capability
        return capability.resolve_channel(conn, requirement, target or {}, _ADAPTERS)

    req_type = requirement.get("type")
    candidates = [
        a for a in _ADAPTERS
        if (not a.serves or req_type in a.serves)   # serves 空 = 全要求を処理可
        and a.available_at(target)
    ]
    if not candidates:
        # フォールバック: available_at 無視して全アダプタ → 先頭。現行は Slack。
        candidates = list(_ADAPTERS)
    if not candidates:
        raise RuntimeError("実働チャンネル（アダプタ）が登録されていません")
    return max(candidates, key=lambda a: a.priority)


def deliver(requirement, target: dict = None, conn=None) -> dict:
    """作動: 要求を IF を跨いで実働に届ける（§8.2）。

    Args:
        requirement: 要求 dict（text/blocks/card/type/target/context）
            または str（= text の略）。
        target: 解決の文脈（entity の 3-level ID）。requirement["target"] も許容。
        conn: DB 接続。与えれば能力事実（記号化事実）から自動解決（②）。

    Returns:
        アダプタの send 結果（{"success": True} / {"success": False, "error": ...}）。
    """
    if isinstance(requirement, str):
        requirement = {"text": requirement}
    if target is None:
        target = requirement.get("target")
    adapter = _resolve(requirement, target, conn)
    logger.debug(f"[IF膜] 作動: {adapter.name} に届ける（type={requirement.get('type')}）")
    return adapter.send(requirement)
