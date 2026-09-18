#!/usr/bin/env python3
"""
humos.py — HUMOS（土壌 / 状態空間管理基盤）: 両界に開いた状態空間の「正」

TS（真界/TrueNet）の構成（ts/真界.md）:
    真界 = 記号界 + 物理界
    記号界 = HUMOS（SSS + STN）+ IF + SOS

HUMOS は「脳が住む土壌（humus）」。STN（状態遷移ネット = jobnet.py）が
構造（骨格）なら、HUMOS は**生きた状態（マーキング）**を保持する土壌。
STN は HUMOS の中を流れるトークンを見て発火する。物理界は HUMOS の外。
両者を接合するのは HUMOS だけ（間接的開き）。

## 共有 OS コア（humos_os）へのシム化 — 真界の完成

本モジュールは**ドメイン無依の共有 OS コア `humos_os`（/app/humos_os）への薄い
ラッパー**になった。マーキング・発火ログ・作動ログは**in-memory シングルトン
から DB（data/stb.db の 5 テーブル place/token/marking/firing/writing）へ
永久化**された（net_id=subject_id="mikakura"）。これにより不変条件5「HUMOS のマーキング ≡
M0 からのリプレイ」を機械的に検証可能にし（verify_replay）、DC と**同一の
HUMOS OS**で稼働する（DC は hard_consume=True、STB は False）。

- 共有コア: `humos_os.humos`（5 テーブルの読み書き・fire/write_sos/place_token/
  enabled/verify_replay/replay_marking/clear_transient）。
- 発火ループ: `humos_os.stn.run_net`（jobnet.run_net が委譲）。
- 本モジュールは**従来 API（conn なし）をそのまま維持**し、呼び出し側
  （server.py / state.py / agentic_chat/nodes.py / sos/slack.py）を無変更で
  動かす。conn は本モジュール内で遅延取得（_conn）。

## マーキングストア（state.py から昇格・DB 化）

前段の 2 トークン（field_type / pest_matrix）はかつてトップレベル state.py の
シングルトン `_tokens_store` が管理していた。石1 で humos に昇格し、今回
**DB の marking テーブル（net_id=subject_id="mikakura"）に永久化**。state.py は本モジュール
への純再エクスポート・シムに落ちる（双シングルトン化を避けるため dict ラップ・
値コピーは禁止）。

## 配置・依存（双シングルトン・循環 import の防止）

- **単一モジュール**（humos/ パッケージにしない）。`import humos` が軽依存
  （sqlite3/logging）＋共有コア humos_os の import のみに留まる。
- **jobnet を関数内 import のみ**（enabled 内）。モジュールレベルで import
  すると jobnet → humos → jobnet の循環になる。
- state.py は `from humos import ...` の純再エクスポートのみ。
"""

import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# =====================================================================
# 共有 OS コア（humos_os）の import
# =====================================================================
# humos_os は pip パッケージとして本プロジェクトの venv（.venv）に**個別に**
# インストールされる（DC と同じバージョン 1.4.0 を各自が持つ）。
# sys.path の操作は不要。venv の python で起動すること（起動.txt 参照）。
from humos_os import humos as _h  # noqa: E402  (個別インストールした OS コア)

# =====================================================================
# インスタンス・DB 接続
# =====================================================================
# net_id: STB は単一インスタンス（現行シングルトン意味論を保持）。
# net_id = subject_id（事業主体 三果倉）— オントロジー根底改修（4-level ID）。
_NET_ID = "mikakura"
# 前段 2 トークン（keep_keys = M0 の seed。実行をまたいで再利用される入力）。
_TOKEN_KEYS = frozenset(["field_type", "pest_matrix"])

# 遅延 DB 接続（スレッドローカルで保持）。data/stb.db（server.py と同一パス）。
# テストは STB_HUMOS_DB 環境変数で一時DBに差し替える（本番 data/stb.db を触らない）。
_DB_PATH = os.environ.get("STB_HUMOS_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "stb.db")
_conn_local = __import__("threading").local()


def _ensure_net_places(conn) -> None:
    """STB NET の place スキーマを HUMOS に保証（冪等 upsert）。

    API の set_token（前段トークン投入）は run_net より前に呼ばれるため、その時点で
    place テーブルが空だと place_token が失敗する。jobnet は関数内 import で循環
    回避（humos は既にロード済みなので安全）。
    """
    import jobnet
    _h.ensure_places(conn, {
        pid: {"name": p.get("name", pid), "kind": p.get("kind", "state"),
              "capacity": p.get("capacity"), "token_key": p.get("token_key")}
        for pid, p in jobnet.NET["places"].items()
    })


def _conn() -> sqlite3.Connection:
    """STB の HUMOS DB 接続を遅延取得（スレッドローカル・row_factory=Row）。

    5 テーブル（humos_os.schema）と STB NET の place スキーマが未作成ならここで
    保証（init_db と同一 DDL・冪等）。
    """
    c = getattr(_conn_local, "conn", None)
    if c is None:
        c = sqlite3.connect(_DB_PATH)
        c.row_factory = sqlite3.Row
        from humos_os import schema as _schema
        _schema.ensure_schema(c)
        _ensure_net_places(c)
        _conn_local.conn = c
    return c


def get_conn() -> sqlite3.Connection:
    """STB の HUMOS DB 接続を公開取得（jobnet.run_net が共有 OS の発火ループに渡す）。"""
    return _conn()


def get_net_id() -> str:
    """STB の HUMOS インスタンス ID（net_id=subject_id="mikakura"）。"""
    return _NET_ID


def _iso_to_epoch(iso: str) -> float:
    """DB の ISO 時刻（"YYYY-MM-DD HH:MM:SS" UTC）を epoch 秒に変換。

    designer の fmtTs は `new Date(ts * 1000)`（epoch 秒）を前提にするため、
    従来 in-memory の `ts`（time.time()）と同一形式を返す。
    """
    try:
        return datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").timestamp()
    except (ValueError, TypeError):
        return 0.0


# =====================================================================
# マーキングストア API（state.py から移管・DB 化）
# =====================================================================

def set_token(key: str, value: str) -> dict:
    """Set a single token. Returns current token state."""
    if key not in _TOKEN_KEYS:
        return {"error": f"unknown token key: {key}. valid keys: {sorted(_TOKEN_KEYS)}"}
    conn = _conn()
    _h.place_token(conn, _NET_ID, key, value)
    marking = _h.get_marking_payloads(conn, _NET_ID)
    ready = all(marking.get(k) is not None for k in _TOKEN_KEYS)
    return {
        "ok": True,
        "key": key,
        "value": value,
        "tokens": {k: marking.get(k) for k in _TOKEN_KEYS},
        "ready": ready,
    }


def get_token_state() -> dict:
    """Get current token state."""
    marking = _h.get_marking_payloads(_conn(), _NET_ID)
    return {
        "tokens": {k: marking.get(k) for k in _TOKEN_KEYS},
        "ready": all(marking.get(k) is not None for k in _TOKEN_KEYS),
    }


def reset_tokens() -> dict:
    """Reset all tokens."""
    conn = _conn()
    for k in _TOKEN_KEYS:
        _h._unmark(conn, _NET_ID, k)
    conn.commit()
    return {"ok": True, "tokens": {k: None for k in _TOKEN_KEYS}, "ready": False}


def get_required_keys() -> frozenset:
    """Return the set of required token keys."""
    return _TOKEN_KEYS


# =====================================================================
# 石1 の 3 関数
# =====================================================================

def fire(transition: str, consumed: dict, generated: dict) -> None:
    """トランジション発火を記録し、マーキングを更新する（書き込み: STN → HUMOS）。

    jobnet.run_net の発火ループから各トランジション発火時に呼ばれる。
    `generated`（post プレースのトークン）をライブマーキングに反映し、
    発火ログ（消費・生成・時刻）を記録する。STB はソフト消費（hard_consume=False、
    pre を残す＝分岐が同一トークンを共有するため）。
    """
    _h.fire(_conn(), _NET_ID, transition, consumed, generated, hard_consume=False)
    logger.info(f"[HUMOS] fire: {transition}（生成 {list((generated or {}).keys())}）")


def write_sos(action: str, result: dict) -> None:
    """SOS（作動の運動器）の作動結果を記録する（書き込み: STN → HUMOS）。

    「作動完了が状態である」（TS設計 §4.3）の足場。マーキングは変えない
    （作動ログ＝writing のみ）。石3（在庫リフレックス）でこの状態から次トークンを
    発生させる。
    """
    _h.write_sos(_conn(), _NET_ID, action, result)
    ok = bool((result or {}).get("success"))
    logger.info(f"[HUMOS] write_sos: {action}（success={ok}）")


def place_token(key: str, value: Any, trigger: str) -> None:
    """HUMOS が状態判断して次トークンをマーキングに置く（石3・在庫リフレックス）。

    write_sos（記録）とは**別段階**。ts/TS設計.md §4.3「SOS 作動の完了が状態
    である」の 3 段（記録 → 状態判断 → 次トークンを置く）の 3 段目。
    作動（Slack送信）が完了すると HUMOS が状態判断し、後続トランジションの
    入力プレースに彩色トークンを置く。ネットは handler の返り値ではトークンを
    持たず、**ライブマーキングの再読**（jobnet.run_net のループ先頭）で発見する
    （間接的開き・能動ゲートの具体）。

    **発火ログへの追記は行わない**。run_net の発火ループが、handler の発火
    前後のマーキング差分でこの配置を検出し、トリガーしたトランジション
    （trigger）の生成トークンとして発火ログに記録する（リプレイ整合 §7.5）。
    """
    if value not in (None, "", []):
        _h.place_token(_conn(), _NET_ID, key, value)
    logger.info(f"[HUMOS] place_token: {key}（trigger={trigger}）")


def place_external_token(key: str, payload: Any, event: dict = None) -> None:
    """入方向 IF（膜）— ドメインオブジェクトが要求を STN に届ける（石5）。

    出方向（STN→実働）の対。HUMOS のドメインオブジェクト（本圃等）が
    スケジュールで要求（作動）を生成し、IF を跨いで STN を起動する。

    - **マーキング**にトークンを置く（place_external_token が _place_marking）。
    - **firing 行**も記録（transition_id="E:<token_key>"、リプレイ整合・不変条件5）。
    - STN 側の API（IF の接続点）として、ドメインオブジェクトがこれを起動する。

    **再開は resume_net（持続させる主体・監査G3）**。run_net（同期バッチ）は
    clear_transient で外部トークンを消すため、入方向・非同期再開は
    `stn.resume_net`（clear しない発火ループ）で発火させる。

    Args:
        key: トークンキー（対応するプレースが place テーブルに登録されていること）。
        payload: 要求ペイロード（{type, target, context}）。
        event: トリガーイベント（{"source": "domain_object", ...}）。
    """
    _h.place_external_token(_conn(), _NET_ID, key, payload, event)
    logger.info(f"[HUMOS] 入方向 IF: {key} @{_NET_ID}（{(event or {}).get('source', 'external')}）")


def record_return(subject_id: str, domain_id: str, entity_id: str, object_id: str,
                  attr_key: str, attr_value: Any,
                  token_key: str = None, token_payload: Any = None,
                  source: str = "physical_operator") -> dict:
    """帰還 API（§2.4）— 実働完了 → 記録 → ドメインエンティティオブジェクト。

    物理界の実働（人・機械・アプリ）が**終われば**、その**実行記録・状態記録**が
    HUMOS のドメインエンティティオブジェクトに戻る。これが系の帰還側であり、
    「相互開き」が閉じる最後の一段（ts/真界_IF設計.md §2.4）。

    帰還は2段:
      ① **状態記録**（本体）: `entity.set_attribute(attr_kind="state")` で
         ドメインエンティティオブジェクトの現在状態を更新。これが SSS の認知対象
         であり、次の要求（入方向）のトリガーにもなる。系はここでドメインレベルで
         閉じる。
      ② **STN 再開**（帰結）: `token_key` が与えられれば
         `place_external_token`（マーキング変化）→ `resume_net`（持続させる主体）
         で後続 T を発火させる。§2.2 の在庫帰還は②のみの特殊ケース。

    記録主体は人でも機械でもよい（機械がネットにつながっていれば機械自身が
    記録する）。どちらでも①②は同一 API。

    Args:
        subject_id / domain_id / entity_id / object_id: 帰還先のドメインエンティティ
            オブジェクト（4-level ID。例: "mikakura"/"iichigo"/"organization"/"1"
            =那賀地方いちご生産組合連合会）。
        attr_key / attr_value: 状態記録のキーと値（attr_kind="state"）。
        token_key: ②STN 再開の対象トークンキー（None で①のみ）。対応するプレースが
            place テーブルに登録されていること。
        token_payload: ②のトークンペイロード。
        source: 記録主体（"physical_operator"=人 / "machine"=機械 / "manual"=手動）。

    Returns:
        {"state": True, "resumed": <resume_net の state or None>}。
    """
    from humos_os import entity
    # ① 状態記録（本体）— ドメインエンティティオブジェクトの現在状態を更新。
    entity.set_attribute(_conn(), subject_id, domain_id, entity_id, object_id,
                         attr_key, attr_value, attr_kind="state", source=source)
    logger.info(f"[HUMOS] 帰還① 状態記録: {subject_id}/{domain_id}/{entity_id}/{object_id} "
                f"{attr_key}={attr_value}（{source}）")
    # ② STN 再開（帰結）— マーキング変化で後続 T を発火させる。
    resumed = None
    if token_key is not None:
        place_external_token(token_key, token_payload,
                             event={"source": source})
        import jobnet
        resumed = jobnet.resume_net()
        logger.info(f"[HUMOS] 帰還② STN 再開: {token_key} @{_NET_ID}")
    return {"state": True, "resumed": resumed}


def enabled(transition: str) -> bool:
    """トランジションの発火可能判定（読み出し: HUMOS → STN）。

    enabled_when に列挙したトークンキーが**ライブマーキング**（今この値があるか）
    で**すべて**揃っている（None/""/[] 以外）とき発火可能。enabled_when が空なら
    常に発火可能とみなす。

    ※ jobnet.is_enabled と同じ判定だが、供給元を**HUMOS のライブマーキング**
      （DB）で判定する。関数内 import（循環回避: jobnet → humos はモジュール
      レベル、humos → jobnet は実行時）。
    """
    import jobnet
    required = jobnet.NET["transitions"][transition].get("enabled_when", [])
    return _h.enabled(_conn(), _NET_ID, required)


# =====================================================================
# 読み出し・検証
# =====================================================================

def get_marking() -> dict:
    """現在のマーキング（M）のコピーを返す（token_key → 値）。"""
    return _h.get_marking_payloads(_conn(), _NET_ID)


def get_firing_log() -> list:
    """発火ログのコピーを返す（時系列）。

    従来 in-memory の形状 {tid, consumed, generated, ts} を維持する（designer
    の langgraph_designer.html が e.tid / e.generated / e.ts を読む）。ts は
    epoch 秒（fmtTs が `new Date(ts * 1000)` を前提）。
    """
    out = []
    for e in _h.get_firing_log(_conn(), _NET_ID):
        out.append({
            "tid": e.get("transition_id"),
            "consumed": e.get("consumed") or {},
            "generated": e.get("produced") or {},
            "ts": _iso_to_epoch(e.get("fired_at")),
        })
    return out


def get_sos_log() -> list:
    """作動ログのコピーを返す（時系列）。

    従来 in-memory の形状 {action, result, ts} を維持する（designer が
    e.action / e.result / e.ts を読む）。ts は epoch 秒。
    """
    out = []
    for e in _h.get_writing_log(_conn(), _NET_ID):
        if e.get("operator_kind") != "sos":
            continue
        out.append({
            "action": e.get("action"),
            "result": e.get("payload") or {},
            "ts": _iso_to_epoch(e.get("created_at")),
        })
    return out


def get_writing_log() -> list:
    """書き込みログ（空間状態の生データ・SSS の認知対象）のコピーを返す（時系列）。

    全 operator_kind を返す（transition / perception / evaluation / decision /
    projection / sos / physical_operator / external_event）。形状は
    {operator_kind, action, payload, related_place_id, ts}。ts は epoch 秒。
    SOS ログ（get_sos_log）は本ログの operator_kind=="sos" の部分集合。
    """
    out = []
    for e in _h.get_writing_log(_conn(), _NET_ID):
        out.append({
            "operator_kind": e.get("operator_kind"),
            "action": e.get("action"),
            "payload": e.get("payload") or {},
            "related_place_id": e.get("related_place_id"),
            "ts": _iso_to_epoch(e.get("created_at")),
        })
    return out


def replay_marking() -> dict:
    """M0（前段 2 トークンの現在値）から発火ログを再生してマーキングを復元する。

    内部整合の機械的検証（TS設計 §7.5「HUMOS のマーキング ≡ M0 からのリプレイ」）
    の足場。発火ログを時系列で再生し、得られるマーキング（token_key → 値）を
    返す。DB 永続化後も get_marking() と一致するはず（不一致=整合違反）。

    M0 は**前段 2 トークン（_TOKEN_KEYS）の現在値**（set_token で投入された
    初期マーキング）で、発火ログは後段トークンの生成のみを記録する。
    したがってリプレイは _TOKEN_KEYS の現在値から開始し、発火ログの生成を
    適用する（前段トークンは発火ログに現れないため）。
    """
    replayed_ids = _h.replay_marking(_conn(), _NET_ID, hard_consume=False,
                                     keep_keys=_TOKEN_KEYS)
    live = _h.get_marking_payloads(_conn(), _NET_ID)
    # token_id → token_key の逆引きで、再生値を「値」に復元（従来形状を維持）。
    out = {}
    for tk in set(list(replayed_ids) + list(live)):
        tid = replayed_ids.get(tk)
        if tid is None:
            continue
        row = _conn().execute(
            "SELECT payload FROM token WHERE net_id=? AND token_id=?",
            (_NET_ID, tid)).fetchone()
        if row and row["payload"]:
            import json
            try:
                out[tk] = json.loads(row["payload"])
            except (ValueError, TypeError):
                out[tk] = row["payload"]
    return out


def clear_transient_tokens() -> None:
    """入力トークン（_TOKEN_KEYS）以外のマーキングを消す（石3）。

    run_net の開始時に呼ぶ。前走の出力トークン（inventory_token /
    external_wait_token 等）が DB の marking に残ると、次走の M0 がそれを seed
    して後続トランジション（T_inventory 等）が前走の処方で**先発火**する
    （ストールトークンバグ）。発火ログ・作動ログも一緒にクリアし、リプレイ整合
    （§7.5）を実行単位に保つ。
    """
    _h.clear_transient(_conn(), _NET_ID, keep_keys=_TOKEN_KEYS)


def reset() -> None:
    """マーキング・発火ログ・作動ログを初期化する（テスト用）。"""
    _h.reset_instance(_conn(), _NET_ID)
