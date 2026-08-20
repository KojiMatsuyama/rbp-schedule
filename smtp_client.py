#!/usr/bin/env python3
"""
smtp_client.py — Gmail (App Password) を使ったメール送信クライアント

.env または環境変数から SMTP 設定を読み込み、
smtplib でメールを送信する。
"""

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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


def _get_smtp_config() -> Optional[dict]:
    """
    Load SMTP config from environment variables or .env file.

    Returns dict with keys: smtp_host, smtp_port, sender_email, app_password
    Returns None if not configured.
    """
    # Try environment variables first
    host = os.environ.get("SMTP_HOST", "").strip()
    port_str = os.environ.get("SMTP_PORT", "").strip()
    sender = os.environ.get("SENDER_EMAIL", "").strip()
    password = os.environ.get("SENDER_APP_PASSWORD", "").strip()

    # Fallback: read .env file
    if not host:
        env = _load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
        host = env.get("SMTP_HOST", "").strip()
        port_str = env.get("SMTP_PORT", "").strip()
        sender = env.get("SENDER_EMAIL", "").strip()
        password = env.get("SENDER_APP_PASSWORD", "").strip()

    if not all([host, port_str, sender, password]):
        return None

    return {
        "smtp_host": host,
        "smtp_port": int(port_str),
        "sender_email": sender,
        "app_password": password,
    }


def is_configured() -> bool:
    """Check if SMTP is configured."""
    return _get_smtp_config() is not None


def send_email(to_addr: str, subject: str, body_html: str, body_text: str = None) -> dict:
    """
    Send an email via SMTP.

    Args:
        to_addr: Recipient email address
        subject: Email subject
        body_html: HTML body content
        body_text: Plain text body (fallback if None)

    Returns:
        {"success": True} or {"success": False, "error": "..."}
    """
    cfg = _get_smtp_config()
    if cfg is None:
        return {
            "success": False,
            "error": "SMTPが設定されていません。.env に SMTP_HOST, SMTP_PORT, SENDER_EMAIL, SENDER_APP_PASSWORD を記載してください。",
        }

    if body_text is None:
        body_text = body_html

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["sender_email"]
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.starttls(context=context)
            server.login(cfg["sender_email"], cfg["app_password"])
            server.sendmail(cfg["sender_email"], to_addr, msg.as_string())
        return {"success": True}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "SMTP認証に失敗しました。App Passwordを確認してください。"}
    except smtplib.SMTPException as e:
        return {"success": False, "error": f"SMTPエラー: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"メール送信中にエラーが発生しました: {str(e)}"}
