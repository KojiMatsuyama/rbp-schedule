#!/usr/bin/env python3
"""記号化事実（能力事実）のデータ基盤 — チャネル解決（②）の根拠。

IF 設計（[ts/真界_IF設計.md](../ts/真界_IF設計.md) §8.9・§8.10）:
  チャネル解決（②）の根拠は**記号化された事実（データ）**。
  人は「ルーティングのルール」ではなく「事実の記号化」をする。

本モジュールは `entity_attribute`（attr_kind=state）への**薄いアクセス層**。
能力事実（導入済み/未導入・担当者等）を登録・読み取り、チャネル解決
（resolve_channel）に供する。

**フェーズ A（記号化事実・石5 に依存しない）**: チャネル解決（②）の
データ基盤。石5（入方向）が要求を STN に届ける前に先行できる。

レイヤ:
  humos_os.entity — entity_attribute の CRUD（set_attribute / get_attributes）
  capability.py   — 本ファイル。能力事実（attr_kind=state）への薄いアクセス層
  sos.membrane    — IF ファサード（② D: resolve_channel を使う）
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_KIND = "state"   # 能力事実 = 状態（SSS が認知）。attr_kind=state


def set_capability(conn, subject_id: str, domain_id: str, entity_id: str,
                   object_id: str, key: str, value: Any,
                   source: str = "manual") -> None:
    """能力事実を 1 件登録・更新する（冪等 upsert）。

    例: set_capability(conn, "mikakura", "iichigo", "field", "2",
                       "inventory_app", "deployed")
        = 本圃（object_id=2）に在庫アプリが導入済み。

    人は**ルーティングのルールではなく事実の記号化**をする（§8.1）。
    """
    from humos_os import entity
    entity.set_attribute(conn, subject_id, domain_id, entity_id, object_id,
                         key, value, attr_kind=_KIND, source=source)


def get_capability(conn, subject_id: str, domain_id: str, entity_id: str,
                   object_id: str, key: str) -> Optional[str]:
    """能力事実の値を読む。未登録（または attr_kind が state でない）なら None。"""
    from humos_os import entity
    attrs = entity.get_attributes(conn, subject_id, domain_id, entity_id, object_id)
    row = attrs.get(key)
    if row is None or row.get("attr_kind") != _KIND:
        return None
    return row.get("attr_value")


def has_capability(conn, subject_id: str, domain_id: str, entity_id: str,
                   object_id: str, key: str, value: str) -> bool:
    """能力事実が (key, value) で登録されているか。"""
    return get_capability(conn, subject_id, domain_id, entity_id, object_id,
                          key) == value


def list_capabilities(conn, subject_id: str, domain_id: str, entity_id: str,
                      object_id: str) -> dict:
    """オブジェクトの能力事実（attr_kind=state）を {key: value} で返す。"""
    from humos_os import entity
    attrs = entity.get_attributes(conn, subject_id, domain_id, entity_id, object_id)
    return {k: v.get("attr_value") for k, v in attrs.items()
            if v.get("attr_kind") == _KIND}


def resolve_channel(conn, requirement: dict, target: dict, adapters: list):
    """チャネル解決（② D）— 能力事実（記号化事実）からアダプタを選ぶ。

    serves ∩ 能力事実（requires）のマッチング＋優先度（max）＋フォールバック。

    アダプタは `requires`（dict: capability_key -> 必要な値）で**能力事実の
    要件**を自己記述する（§8.3）。例:
        class AppAPIAdapter: requires = {"inventory_app": "deployed"}
    requires が空（None）のアダプタは常に可用（Slack フォールバック）。

    Args:
        conn: DB 接続（humos.get_conn()）。
        requirement: 要求 dict（type = 要求の種）。
        target: 解決の文脈（{"subject_id","domain_id","entity_id","object_id"}）。
        adapters: 候補アダプタの list。

    Returns:
        解決されたアダプタ。

    Raises:
        RuntimeError: アダプタが 1 つも登録されていない場合。
    """
    req_type = requirement.get("type")
    sid, did, eid, oid = (target.get("subject_id"), target.get("domain_id"),
                          target.get("entity_id"), target.get("object_id"))

    def _available(a) -> bool:
        requires = getattr(a, "requires", None) or {}
        for key, value in requires.items():
            if not has_capability(conn, sid, did, eid, oid, key, value):
                return False
        return True

    candidates = [
        a for a in adapters
        if (not a.serves or req_type in a.serves)
        and _available(a)
    ]
    if not candidates:
        candidates = list(adapters)   # フォールバック（能力事実無視して先頭）
    if not candidates:
        raise RuntimeError("実働チャンネル（アダプタ）が登録されていません")
    return max(candidates, key=lambda a: a.priority)
