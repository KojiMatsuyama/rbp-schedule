#!/usr/bin/env python3
"""
chat_client.py — Slack Incoming Webhook 経由でメッセージを送信

SlackのチャンネルにWebhook URLを設定し、
HTTP POST でメッセージを送信する。
SMTP不要。
"""

import json
import os
import urllib.request
import urllib.error
from typing import Optional


def _load_env(path: str) -> dict:
    """Simple .env file parser."""
    env = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _get_webhook_url() -> Optional[str]:
    """
    Load Slack Webhook URL from environment variable or .env file.

    Returns the webhook URL string, or None if not configured.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if url:
        return url

    env = _load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    url = env.get("SLACK_WEBHOOK_URL", "").strip()
    if url:
        return url

    return None


def is_configured() -> bool:
    """Check if Slack Webhook is configured."""
    return _get_webhook_url() is not None


def send_message(text: str, blocks: list = None) -> dict:
    """
    Send a message to Slack via Incoming Webhook.

    Args:
        text: Plain text message
        blocks: Optional list of Block Kit elements (Slack Block Kit JSON format)

    Returns:
        {"success": True} or {"success": False, "error": "..."}
    """
    webhook_url = _get_webhook_url()
    if webhook_url is None:
        return {
            "success": False,
            "error": "Slack Webhook URLが設定されていません。.env に SLACK_WEBHOOK_URL を記載してください。",
        }

    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"success": True}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"success": False, "error": f"HTTP {e.code}: {body[:200]}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"ネットワークエラー: {str(e.reason)}"}
    except Exception as e:
        return {"success": False, "error": f"送信エラー: {str(e)}"}


def send_message_with_card(title: str, sections: list, footer: str = None) -> dict:
    """
    Send a rich message with a Slack Section Blocks.

    Args:
        title: Message title
        sections: List of section dicts, each with:
            header: Section header text
            fields: List of field dicts (text key, optional value)
        footer: Optional footer text

    Returns:
        {"success": True} or {"success": False, "error": "..."}
    """
    blocks = []

    # Header text
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}})

    # Sections
    for sec in sections:
        header = sec.get("header", "")
        fields = sec.get("fields", [])
        if header:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{header}*"}})
        for field in fields:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": field.get("text", "")},
            })

    # Footer
    if footer:
        blocks.append({"type": "divider"})
        blocks.append({"type": "context", "elements": [{"type": "plain_text", "text": footer}]})

    return send_message(text=title, blocks=blocks)
