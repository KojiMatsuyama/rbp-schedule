#!/usr/bin/env python3
"""SOS 実働チャンネル — Slack 送信プログラム。

SOS（System Operation Software）ライブラリが管理する「実働」の一種。
投射モジュール(projection.py)が「算出結果 → 文章」の写像（投射）なら、
本チャンネルは「文章 → Slack」への投映（作動）で、ナビゲーターネット →
物理界の境界の出口に相当する。

レイヤ構成:
  projection.py    — 文章生成（RBP結果の写像・投射）
  sos/ (本パッケージ) — 実働管理ライブラリ。Slack を実働チャンネルとして管理
  sos.slack (本ファイル) — Slack 送信実働
  chat_client.py   — HTTP トランスポート（Webhook POST・低レベル）

本ファイルは HTTP トランスポートを伴う入口として、
send_message / send_card / is_configured を一箇所に集約し、
chat_client への唯一の委譲先となる。

呼び出し元（agentic_chat / scripts/rx_prescribe / mcp_tools / server）は
`import sos` 経由で `sos.slack.send_message(...)` を使う。
`import sos` は agentic_chat/__init__.py（=LangGraph全套）を巻き込まないため、
三方向から同じ APP_ROOT 解決で通る。
"""

import logging

import chat_client

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """Slack Webhook が設定されているか。"""
    return chat_client.is_configured()


def send_message(text: str, blocks: list = None) -> dict:
    """
    文章を Slack に送信する（作動）。

    Args:
        text: 送信する本文。
        blocks: 任意の Block Kit 要素リスト。

    Returns:
        {"success": True} or {"success": False, "error": "..."}
    """
    try:
        result = chat_client.send_message(text, blocks=blocks)
        return result if isinstance(result, dict) else {"success": False, "error": "不正な返り値"}
    except Exception as e:
        logger.error(f"Slack送信エラー: {e}")
        return {"success": False, "error": str(e)[:200]}


def send_card(title: str, sections: list, footer: str = None) -> dict:
    """
    リッチカード（Section Blocks）を Slack に送信する（作動）。

    Args:
        title: カードタイトル。
        sections: [{"header": "...", "fields": [{"text": "..."}, ...]}, ...]
        footer: 任意のフッター文字列。

    Returns:
        {"success": True} or {"success": False, "error": "..."}
    """
    try:
        result = chat_client.send_message_with_card(title, sections, footer)
        return result if isinstance(result, dict) else {"success": False, "error": "不正な返り値"}
    except Exception as e:
        logger.error(f"Slackカード送信エラー: {e}")
        return {"success": False, "error": str(e)[:200]}
