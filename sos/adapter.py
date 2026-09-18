#!/usr/bin/env python3
"""SOS 実働チャンネルのアダプタ抽象（IF 設計 §8）。

「作動（起動）が IF を跨いで実働に届く」ための**差し替え層**。
1 アダプタ = 1 つの「届ける方法」（実働チャンネル）。現行は Slack のみ。

レイヤ構成（IF 設計 §8.2 責務の分離）:
  sos.slack    — Slack 送信実働（既存・write_sos 込み）。そのまま使う。
  sos.adapter  — 本ファイル。実働チャンネルをアダプタとして包む。
  sos.membrane — IF ファサード（deliver）。アダプタを選んで届ける。

アダプタは**自己記述**する（§8.3）:
  serves       — 何を処理できるか（要求の種）。現行はチャネル選択が未実装なので
                 空（= 全要求を処理可）。将来の自動チャネル解決（§8.10）で
                 serves ∩ available_at のマッチングに使う。
  available_at — どこで使えるか（記号化事実から判定）。現行は常に True。

**挙動不変リファクタ（①）**: 本層は既存の `sos.slack.send_message` を
包むだけ。write_sos（作動ログ）は `sos.slack` 内で呼ばれるため、経路を
`nodes.py::_send_to_slack` → `sos.membrane.deliver` に切替えても作動ログは
1 回だけ（二重記録しない）。
"""

from typing import Optional

import logging

logger = logging.getLogger(__name__)


class BaseAdapter:
    """実働チャンネルのアダプタ基底。

    自己記述（§8.3）:
      serves       — 処理できる要求の種（set[str]）。空 = 全要求を処理可。
      priority     — 解決時の優先度（大きいほど優先）。記符号的実働 > 物理的実働。
      requires     — 使えるために必要な**能力事実**（dict: capability_key -> 値）。
                     宣言的な可用性（§8.3）。解決器（capability.resolve_channel）が
                     entity_attribute（attr_kind=state）で照会する。空 = 常に可用。
      available_at — 対象（target）で使えるか（カスタム判定）。requires と併用（AND）。
                     能力事実の照会を宣言（requires）でなくコードで書く場合。
    """

    serves: set = frozenset()
    priority: int = 0
    name: str = "base"
    requires: dict = {}   # 能力事実の要件（宣言的な可用性）。空 = 常に可用。

    def available_at(self, target: Optional[dict]) -> bool:
        """対象（entity の 3-level ID 等）で使えるか。デフォルトは常に使える。"""
        return True

    def send(self, requirement: dict) -> dict:
        """要求を届ける（作動＝膜の通過）。実装はサブクラス。"""
        raise NotImplementedError


class AppAPIAdapter(BaseAdapter):
    """業務アプリ API 実働（記符号的実働・§8.3 の具体例）。

    記号化された事実（`inventory_app=deployed`）がある圃場では、Slack（物理的実働）
    より優先して業務アプリ API へ届ける。`requires` で**能力事実の要件**を自己記述し、
    解決器（capability.resolve_channel）が entity_attribute で照会する（§8.10）。

    **現行は未登録**（membrane._ADAPTERS に載せない）。業務アプリ API の実装が
    揃い次第、`membrane._ADAPTERS` に登録すると、導入済み圃場への在庫確認が
    自動でこのチャネルにルーティングされる（②の自動解決が効く）。
    """

    serves = frozenset({"inventory_check"})
    priority = 10   # Slack(0) より優先（記符号的実働 > 物理的実働）
    name = "app_api"
    requires = {"inventory_app": "deployed"}   # 導入済みの圃場でのみ可用

    def send(self, requirement: dict) -> dict:
        """業務アプリ API に届ける（実装は API 接続の整備と同時）。"""
        raise NotImplementedError("AppAPIAdapter.send は業務アプリ API の実装待ち")


class SlackAdapter(BaseAdapter):
    """Slack 送信実働（物理的実働・フォールバック）。

    既存の `sos.slack.send_message` を包む。write_sos（作動ログ）は
    `sos.slack.send_message` 内で呼ばれる（本アダプタでは呼ばない=二重記録防止）。
    """

    serves = frozenset()   # 空 = 全要求を処理可（チャネル選択未実装）
    priority = 0
    name = "slack"

    def send(self, requirement: dict) -> dict:
        """要求を Slack に送信する。

        requirement:
          text   — 送信する本文（必須）。
          blocks — 任意の Block Kit 要素。
          card   — {"title","sections","footer"}。あれば send_card で送信。
        """
        # sos.slack は APP_ROOT 由来。本ファイルは sos/ 配下なので親ディレクトリを path に加える。
        import os
        import sys
        _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _app_root not in sys.path:
            sys.path.insert(0, _app_root)
        import sos.slack

        card = requirement.get("card")
        if card:
            return sos.slack.send_card(
                card.get("title"),
                card.get("sections", []),
                card.get("footer"),
            )
        return sos.slack.send_message(requirement.get("text", ""), blocks=requirement.get("blocks"))
