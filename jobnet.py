#!/usr/bin/env python3
"""
jobnet.py — 状態遷移ネット（State Transition Net / STN）: プレース↔トランジション構造の「正」

三層構造（状態空間データネット × ナビゲーターネット × 物理界）における
**ナビゲーターネット** の宣言的定義。プレース（状態: トークン集約）・
トランジション・弧（接続）・複合トランジションを**単一のデータ構造
（NET dict）**として記述し、ここを唯一無二の正（source of truth）とする。

**トランジションの普遍構造**（NET["universal_transition"]）:
  T ＝ { 認知, 評価, 決定, 投射 } ＋ 作動
  各 T が発火するとこのパイプラインを**内部で**走らせる。これは T の分類でなく、
  **全 T に共通する内部構造（発火のセマンティクス）**であり、最終到達点の
  **作動（物理界的作動）が T の正体**である。各業務トランジション（薬剤選定・
  在庫チェック・防除実施・検鏡・定植…）はこの普遍構造をその業務で具現化
  （パラメータ化）したもの。NET["composite"] の「薬剤選定」はその最初の具現化。

本モジュールは NET から 3 つに派生させる:
  1. run_net(state) — NET を**マーキング駆動の発火ループ**で実行（石2）。
     スケジューラ = 状態遷移ネット本体（LangGraph は廃止された）。
  2. net_to_json() — プレース/トランジション/弧/複合/作動/発火可能を視覚化
     向け JSON に（→ server.py の GET /api/net → langgraph_designer.html）。
  3. net_state()/net_firing() — 現在のプレース値から「どのトランジションが
     発火可能か」を判定（真の Petri 網発火ルールの宣言・視覚化）。

## なぜトップレベル・正を Python に置くか
- `agentic_chat/` を import すると `agentic_chat/__init__.py`（=jobnet→nodes
  全套）が引かれる（perception/evaluation/decision/projection/state と同じ
  理由）。正となるネット定義はトップレベルに置く。
- 設計の決定: **正=Pythonモジュール**（宣言的 NET）。designer は NET を
  read-only で視覚化する。実行スケジューラも NET 自身（run_net）。
- 発火ルール: **実行スケジューラ = 状態遷移ネット本体**（run_net が
  **マーキング駆動の発火ループ**で各トランジションの handler を発火）。
  LangGraph は廃止された。`enabled_when` は「入力プレースのトークンが
  揃ったら発火可能」という真の Petri 網発火ルールの根拠であり、run_net が
  これをライブマーキングで判定して発火する（石2）。NET["edges"] は視覚化・
  構造検証用のみ（実行トポロジーではなくなった）。

## トランジション本体（handler）
各トランジションの `handler` は「モジュール.関数名」の文字列（遅延解決）。
agentic_chat/nodes.py の薄いアダプタをそのままトランジション本体として
再利用する。nodes.py は無変更。
"""

import importlib
import logging

import humos
from humos_os import stn  # noqa: E402  (共有 OS の発火ループ。humos の import が /app を path に加える)

logger = logging.getLogger(__name__)

# =====================================================================
# NET — プレース↔トランジション構造の「正」（宣言的データ）
# =====================================================================
# places      : プレース（状態: トークン集約）。token_key は HUMOS（humos._marking）
#               or ChatState のどちらを指すか store で明示。
# transitions : トランジション。handler=本体関数、pre/post=弧、enabled_when=
#               発火に必要なトークンキー（発火ルールの宣言）、actions=作動(SoS)。
# edges       : 実行トポロジー（トランジション間の有向エッジ）。run_net が
#               このエッジでスケジューリングする（decision の post が2プレース
#               → 2分岐を再現）。
# entry/ends  : エントリー・終了トランジション。
# composite   : 複合トランジション（視覚化用グループ）。薬剤選定=内部チェーン
#               （認知─評価─決定─投射）＋作動(Slack)。実行時は inner を展開。

NET = {
    "name": "薬剤選定状態遷移ネット（STN）",
    "model": "petri_net_composite_transition",

    # ---- トランジションの普遍構造（全 T に共通する内部構造） ----
    # T ＝ { 認知, 評価, 決定, 投射 } ＋ 作動
    # 各 T が発火するとこのパイプラインを内部で走らせる。T の分類ではなく、
    # 全 T に共通する発火のセマンティクス。最終到達点の作動（物理界的作動）
    # が T の正体（「この物理的作動を成すために T は存在する」）。
    # 実行は平坦な transitions（humos_os.stn.run_net が消費）で行うため、
    # ここは**宣言的メタデータ**（視覚化・ドキュメント・将来の具現化生成用）。
    # 各業務 T はこの universal_transition をその業務で具現化（パラメータ化）。
    "universal_transition": {
        "stages": [
            {"key": "perception",  "name": "認知", "role": "記号", "module": "perception"},
            {"key": "evaluation",  "name": "評価", "role": "記号", "module": "evaluation"},
            {"key": "decision",    "name": "決定", "role": "記号", "module": "decision"},
            {"key": "projection",  "name": "投射", "role": "記号", "module": "projection"},
            {"key": "actuation",   "name": "作動", "role": "物理", "module": "sos",
             "note": "T の正体。STN（トリガー点）→ HUMOS → SOS → IF → 物理界。"},
        ],
        "essence": "actuation",
        "note": "全トランジションの内部構造。作動（物理界的作動）が T の正体。",
    },

    # 発火ルール: 実行スケジューラは NET 本体（run_net のマーキング駆動発火
    # ループ、石2）。enabled_when は「入力プレースのトークンが揃うと発火可能」
    # の真の Petri 網発火ルールの根拠であり、run_net がライブマーキングで判定。
    "firing": {
        "scheduler": "jobnet",
        "note": "発火＝NET 自身（run_net のマーキング駆動発火ループ、石2）。enabled_when は発火可能判定の根拠（マーキング駆動）。LangGraph は廃止。",
    },

    # ---- プレース（状態: トークン集約） ----
    # store が「どこで管理されるか」を示す: humos=前段マーキング（石1 で state.py
    # から昇格）/ state=後段 ChatState フィールド（M0 でクリア）/ run()入口=run() が投入するユーザ入力。
    "places": {
        # 前段（HUMOS のマーキングストア _marking が管理。石1 で state.py から昇格）
        "P_field":      {"name": "圃場種",            "token_key": "field_type",        "store": "humos"},
        "P_pest":       {"name": "病害虫予測行列",    "token_key": "pest_matrix",       "store": "humos"},
        # 認知入力（run() が投入するユーザメッセージ。perception が消費）
        "P_input":      {"name": "発火（認知入力）",  "token_key": "messages",          "store": "run()入口"},
        # 後段（ChatState のフィールドが管理）
        "P_vector":     {"name": "認知ベクトル",      "token_key": "vector",            "store": "state"},
        "P_selection":  {"name": "薬剤選定プレース",  "token_key": "selection_token",   "store": "state"},
        "P_rx":         {"name": "処方",              "token_key": "prescription",      "store": "state"},
        "P_report":     {"name": "投射レポート",      "token_key": "projected_message", "store": "state"},
        # 石3: store を humos に変更。HUMOS が Slack送信完了を状態判断して
        # 処方トークンを置く（place_token）。ネットは handler 返り値ではなく
        # ライブマーキングの再読で在庫トークンを発見する（間接的開き・§4.3）。
        "P_inventory":  {"name": "在庫プレース",      "token_key": "inventory_token",   "store": "humos"},
        "P_inv_result": {"name": "在庫結果",          "token_key": "inventory_message", "store": "state"},
        # 石3: 外部待ち（作動完了）。T_rx_exec（Slack送信処方）の post。
        # 「ネットの状態＝Slack送信完了を待っている」の彩色トークン（§8.2）。
        "P_external_wait": {"name": "外部待ち（Slack送信）", "token_key": "external_wait_token", "store": "humos"},
    },

    # ---- トランジション（handler = agentic_chat/nodes.py の薄いアダプタ） ----
    # pre/post = 弧（トークンデータの流れ）。enabled_when = 入力プレースの
    # トークンキー列（= pre の token_key）。run_net がこれをライブマーキングで
    # 判定して発火する（石2）。非循環（acyclic）で視覚化に安全。
    "transitions": {
        "T_state": {
            "name": "状態（発火）",
            "handler": "state.state_node",
            "pre": ["P_field", "P_pest"],
            "post": ["P_input"],
            "enabled_when": ["field_type", "pest_matrix"],
            # ナレッジ（RBP）＝この T を真の T（接地）にするもの。grounded=写生/接地。
            # task_id = この T が属するタスク（業務）単位。knowledge_table = /knowledge で
            # この T のナレッジを編集する rbp_* テーブル（/knowledge?task=<task_id>&table=<kb>）。
            "task_id": "rx-select",
            "knowledge": "圃場・病害虫データ（field_type / pest_matrix）",
            "knowledge_table": "rbp_domain",
            "grounded": True,
        },
        "T_perception": {
            "name": "認知",
            "handler": "agentic_chat.nodes.perception_node",
            "pre": ["P_input"],
            "post": ["P_vector"],
            "enabled_when": ["messages"],
            "task_id": "rx-select",
            "knowledge": "10次元認知ベクトル（diseases 0-9）",
            "knowledge_table": "rbp_perception_axis",
            "grounded": True,
        },
        "T_evaluation": {
            "name": "評価（要求評価）",
            "handler": "agentic_chat.nodes.evaluation_node",
            "pre": ["P_vector"],
            "post": ["P_selection"],
            "enabled_when": ["vector"],
            "task_id": "rx-select",
            "knowledge": "要求評価BOX（rbp_eval_box・判断知識の正）",
            "knowledge_table": "rbp_eval_box",
            "grounded": True,
        },
        "T_decision": {
            "name": "決定（薬剤選定）",
            "handler": "agentic_chat.nodes.decision_node",
            "pre": ["P_selection"],
            "post": ["P_rx"],
            "enabled_when": ["selection_token"],
            "task_id": "rx-select",
            "knowledge": "RBP行列（要求評価行列＋仕様決定行列・境界言語・Haskell rbp-algebra）",
            "knowledge_table": "rbp_spec_condition",
            "grounded": True,
        },
        "T_projection": {
            "name": "投射1（レポート）",
            "handler": "agentic_chat.nodes.projection_node",
            "pre": ["P_rx"],
            "post": ["P_report"],
            "enabled_when": ["prescription"],
            "task_id": "rx-select",
            "knowledge": "レポートテンプレート（処方・スコア・trace を文章に写像）",
            "knowledge_table": "rbp_projection",
            "grounded": True,
        },
        # 石3: T_projection2（投射2・在庫トークン生成）を T_rx_exec に置き換え。
        # Slack送信（処方）の**作動完了**が HUMOS の状態判断で在庫トークンを置く
        # （「作動完了が状態である」§4.3）。post は P_external_wait のみで、
        # 在庫トークンは handler 返り値を持たず place_token 経由でマーキングに置く。
        "T_rx_exec": {
            "name": "作動：Slack送信（処方）",
            "handler": "agentic_chat.nodes.rx_exec_node",
            "pre": ["P_rx"],
            "post": ["P_external_wait"],
            "enabled_when": ["prescription"],
            "actions": [{"name": "Slack送信", "handler": "sos.slack.send_message"}],
            # 作動＝T の正体。knowledge=作動の実体（物理界的作動）。
            # knowledge_table=rbp_actuation（作動=Tの正体をタスク単位で登録）。
            "task_id": "rx-select",
            "knowledge": "Slack送信（処方）・作動の実体（T の正体）",
            "knowledge_table": "rbp_actuation",
            "grounded": True,
        },
        "T_inventory": {
            "name": "在庫チェック",
            "handler": "agentic_chat.nodes.inventory_node",
            "pre": ["P_inventory"],
            "post": ["P_inv_result"],
            "enabled_when": ["inventory_token"],
            "task_id": "rx-select",
            "knowledge": "在庫データ（在庫チェックの判断）",
            "knowledge_table": "rbp_eval_box",
            "grounded": True,
        },
        "T_inventory_exec": {
            "name": "作動：Slack送信（在庫）",
            "handler": "agentic_chat.nodes.inventory_exec_node",
            "pre": ["P_inv_result"],
            "post": [],
            "enabled_when": ["inventory_message"],
            "actions": [{"name": "Slack送信", "handler": "sos.slack.send_message"}],
            "task_id": "rx-select",
            "knowledge": "Slack送信（在庫結果）・作動の実体（T の正体）",
            "knowledge_table": "rbp_actuation",
            "grounded": True,
        },
    },

    # ---- 構造（視覚化・構造検証用エッジ。実行はマーキング駆動） ----
    # 石2 で run_net は edges を走査せず、マーキング（enabled_when）で発火する。
    # edges は designer の弧表示と構造検証（未知トランジション参照チェック）に
    # 残す。decision の post が2プレース（P_report / P_inventory）→ 2分岐。
    "edges": [
        ["T_state", "T_perception"],
        ["T_perception", "T_evaluation"],
        ["T_evaluation", "T_decision"],
        ["T_decision", "T_projection"],
        ["T_decision", "T_rx_exec"],
        ["T_rx_exec", "T_inventory"],
        ["T_inventory", "T_inventory_exec"],
    ],
    "entry": "T_state",
    "ends": ["T_projection", "T_inventory_exec"],

    # ---- 複合トランジション（視覚化用グループ） ----
    # 薬剤選定プレース → 薬剤選定{ 認知─評価─決定─投射 } ＋ 作動(SoS:Slack)
    # = universal_transition（認知→評価→決定→投射＋作動）の**最初の具現化**。
    # 各業務トランジションはこの普遍構造を具現化した複合トランジションであり、
    # 薬剤選定はその第一例。
    "composite": {
        "薬剤選定": {
            "pre": ["P_selection"],
            "inner": ["T_perception", "T_evaluation", "T_decision", "T_projection"],
            "post": ["P_report"],
            "actions": [
                # 石3: 作動経路 = Slack送信処方(T_rx_exec) → 在庫チェック → Slack送信(在庫結果)
                {"name": "作動：Slack送信処方→在庫→Slack", "handler": "agentic_chat.nodes.rx_exec_node"},
            ],
        }
    },
}


# =====================================================================
# NET 実行（スケジューラ = 状態遷移ネット本体 / 真の Petri 網発火）
# =====================================================================
# ここまで「正 = NET」の宣言のみ。実行は NET 自身が行う（LangGraph は
# 廃止された）。トランジションの `handler` は「モジュール.関数名」の
# 文字列（遅延解決）で、現行の薄アダプタ（agentic_chat/nodes.py）を
# そのまま再利用する。

def _resolve_handler(name: str):
    """「モジュール.関数名」を関数オブジェクトに遅延解決する。

    run_net() 実行時にだけ呼ぶ（module import 時では agentic_chat を
    引かない）。サブモジュール agentic_chat.nodes の import はパッケージ
    親の完全初期化を必要としないため、run_net 時点でも通る。
    """
    mod_name, _, func_name = name.rpartition(".")
    mod = importlib.import_module(mod_name)
    return getattr(mod, func_name)


def run_net(state: dict) -> dict:
    """状態遷移ネットを実行する — マーキング駆動の発火ループ（石2）。

    発火ループ本体（M0 構築・マーキング駆動発火・在庫リフレックス検出・
    リプレイ整合）は**ドメイン無依の共有 OS コア `humos_os.stn.run_net`**
    が担う。本関数は NET（places/transitions）と handler 解決を渡して委譲する
    だけ（STB は DC と同一の HUMOS OS で稼働・net_id=subject_id="mikakura"・ソフト消費）。
    以下はループの設計意図（共有 OS に実装済み）の宣言:

    スケジューラは NET 本体（LangGraph 廃止）。NET["edges"] の DAG 走査
    （Kahn 整列＋エッジ DFS）ではなく、**マーキング（トークンのそろい）を
    読んで発火するループ**（ts/TS設計.md §11）:

        M = M0（初期マーキング）
        loop:
            enabled = [ t | t が未発火 かつ is_enabled(t, M) ]
            if not enabled: break          # 固定点 = ネット停止
            for t in enabled: M = fire(t, M)   # 最大非競合ステップ

    - **発火条件** = enabled_when（入力プレースのトークンキーが M に揃う）。
      トポロジカル順ではなく、マーキングがどのトランジションを有効にするかを
      決定する（§8.6「分岐は構造」）。
    - **分岐**（T_decision → T_projection / T_rx_exec）: 両方が同じ
      prescription を読むが、handler は state を変換しトークンをハード消費
      しないため競合しない → 同一ステップで両方発火。
    - **冪等**: fired 集合で各トランジションは 1 実行につき 1 回のみ発火。
      循環ネットでも無限ループしない。
    - **停止・再開は別機構ではない**（§11.3）: external_wait プレース（石3）に
      到達すると次トランジションの enabled_when が外部トークン待ち → enabled
      なし → 固定点 → 停止。外部イベントがトークンを HUMOS に置くと再評価 →
      再開。
    - **在庫リフレックス（石3）**: T_rx_exec（Slack送信処方）の handler が
      作動完了を HUMOS に記録（write_sos）し、状態判断で在庫トークンを置く
      （place_token）。ループ先頭でライブマーキングを再読して発見し、
      T_inventory が発火する（「作動完了が状態である」§4.3・間接的開き）。
      同期モデルでは Slack が即完了するため一連で走り、非同期化しても
      コード変更なしで実際に停止・再開する。

    M0 の構築:
      0. 前走の出力トークンを clear_transient_tokens() でマーキングから消す
         （ストールトークンバグ防止。発火ログ・作動ログもクリアしリプレイ整合
         を実行単位に保つ）。
      1. 前段トークン（store="humos" の field_type/pest_matrix）を HUMOS から
         seed（run() の state には無いため）。後段 humos プレース
         （P_inventory / P_external_wait）は seed しない（実行中に place_token
         で置かれる）。
      2. 後段トークン（store="state"）を None にクリア（run() が
         vector=[0]*10 をプレースホルダで持つが、これは実トークンではない）。
         これにより各トランジションは入力トークンを生成した直後のイテレーション
         で初めて enabled になる（マーキングが依存を自然に保証）。

    Args:
        state: 初期 state（agentic_chat.state.ChatState の dict 形状）。
               各プレース値（token_key）＋実行データを持つ。

    Returns:
        実行後の state（dict）。入力は変更されない（コピーを返す）。

    Raises:
        ValueError: 未知のエッジ先トランジション（構造検証）。
    """
    trans = NET["transitions"]

    # ---- 構造検証: edges が有効なトランジションを参照するか ----
    for a, b in NET["edges"]:
        if a not in trans or b not in trans:
            raise ValueError(f"未知のトランジションをエッジに参照: {a} -> {b}")

    # ---- 共有 OS コア（humos_os.stn）への委譲 ----
    # 発火ループ本体（M0 構築・マーキング駆動発火・在庫リフレックス検出・
    # リプレイ整合）はドメイン無依の共有 OS `humos_os.stn.run_net` が担う。
    # 本 NET（places/transitions）と handler 解決を渡すだけで、STB は DC と
    # **同一の HUMOS OS**で稼働する（net_id=subject_id="mikakura"・ソフト消費 hard_consume=False）。
    # keep_keys = 前段 2 トークン（field_type/pest_matrix、実行をまたいで再利用）。
    conn = humos.get_conn()
    M = stn.run_net(
        conn,
        net_id=humos.get_net_id(),
        places=NET["places"],
        transitions=trans,
        resolve_handler=_resolve_handler,
        initial_state=state,
        hard_consume=False,
        keep_keys=humos.get_required_keys(),
    )
    logger.info(f"[状態遷移ネット] 実行完了（NET 定義・共有 OS 発火ループ）")
    return M


def resume_net() -> dict:
    """ネットを**持続させる主体**（監査 G3）— 外部イベントで再開する（石5）。

    `run_net`（同期バッチ: clear_transient → M0 → fixpoint）とは違い、
    **clear しない**。現在のマーキングから fixpoint まで発火する。

    - **入方向**: ドメインオブジェクトが `humos.place_external_token` で要求
      トークンを置くと、それが T を enable し、本関数が発火させる。
    - **帰還（§2.4）**: 物理オペレータが実働完了を `place_external_token` で
      置くと、本関数が再開して後続 T を発火させる。

    発火ループ本体（clear しない fixpoint・再発火防止）は共有 OS コア
    `humos_os.stn.resume_net` が担う。本関数は NET と handler 解決を渡して
    委譲するだけ（`run_net` と同一の委譲パターン）。
    """
    conn = humos.get_conn()
    M = stn.resume_net(
        conn,
        net_id=humos.get_net_id(),
        places=NET["places"],
        transitions=NET["transitions"],
        resolve_handler=_resolve_handler,
        hard_consume=False,
    )
    logger.info(f"[状態遷移ネット] 再開完了（NET 定義・共有 OS 発火ループ）")
    return M


# =====================================================================
# NET → 視覚化（JSON）＋発火可能判定
# =====================================================================

def _all_token_keys() -> list:
    """全プレースの token_key（発火判定の対象キー）を返す。"""
    seen = []
    for p in NET["places"].values():
        if p["token_key"] not in seen:
            seen.append(p["token_key"])
    return seen


def net_state(chat_state: dict | None = None) -> dict:
    """現在のプレース値を統合する（HUMOS マーキング ＋ 渡された ChatState）。

    前段の field_type/pest_matrix は HUMOS（humos._marking）が管理（石1 で
    state.py から昇格）、後段は ChatState のフィールドが管理。両方を token_key
    で統合し、「どのトランジションが現在発火可能か」の判定に使う。

    Args:
        chat_state: 実行中の ChatState（dict）。None なら HUMOS マーキングのみ。

    Returns:
        {token_key: 値} の統合 dict。
    """
    merged = dict(humos.get_marking())
    if chat_state:
        for key in _all_token_keys():
            val = chat_state.get(key)
            if val not in (None, "", []):
                merged[key] = val
    return merged


def is_enabled(t_id: str, place_values: dict) -> bool:
    """トランジションの発火可能判定（真の Petri 網発火ルールの宣言）。

    enabled_when に列挙した入力プレースのトークンキーが**すべて**揃っている
    （None/空文字/空リスト以外）とき発火可能。enabled_when が空なら常に
    発火可能とみなす。

    石2 で run_net のマーキング駆動発火ループがこれを呼び、place_values として
    ライブマーキング（実行 state）を渡す。net_firing（視覚化）も同一判定を使う。
    """
    t = NET["transitions"][t_id]
    required = t.get("enabled_when", [])
    if not required:
        return True
    return all(place_values.get(k) not in (None, "", []) for k in required)


def net_firing(place_values: dict | None = None) -> dict:
    """全トランジションの発火可能状態を返す。{tid: bool}"""
    pv = place_values if place_values is not None else net_state()
    return {tid: is_enabled(tid, pv) for tid in NET["transitions"]}


def net_runnability(transitions: dict | None = None) -> dict:
    """稼働ゲート（完全性のゲート）— 全 T がナレッジ＋作動で接地したか機械判定。

    漸増的ライフサイクル（[TS設計.md §12](ts/TS設計.md)）:
    - 普遍構造（認知→評価→決定→投射＋作動）は**骨格**。
    - 各 T が真の T（接地）になるには T 固有の**ナレッジ（RBP）**を埋める。
    - 骨格だけ（ナレッジ未実装）の T は**写生（スケッチ）**。
    - **稼働は別フェーズ**：一連に接地（全 T がナレッジ＋作動を備える）したときのみ。
      不完全なネットは未接地（不変条件1）で TrueNet ではない。

    判定:
    - 各 T の `grounded`（ナレッジ実装済み/未実装）と `knowledge`（何を埋めるか）。
    - 作動 T（`actions` 持ち＝T の正体）は必ず接地必須。
    - `runnable` = 全 T が grounded（一連に接地）。

    Args:
        transitions: 判定対象のトランジション dict。None なら固定 STN
            （NET["transitions"]）。コホートネット（server._cohort_net）は
            生成した transitions を渡す。

    Returns:
        {
          "runnable": bool,          # 稼働可能（全 T 接地）か
          "grounded": [tid, ...],    # 接地済み（真の T）
          "sketch":   [tid, ...],    # 写生（ナレッジ未実装・まだ真の T でない）
          "actuations": [tid, ...],  # 作動 T（T の正体・末端）
          "total": int, "grounded_count": int,
          "note": str,
        }
    """
    trans = transitions if transitions is not None else NET["transitions"]
    grounded, sketch, actuations = [], [], []
    for tid, t in trans.items():
        if t.get("actions"):
            actuations.append(tid)
        if t.get("grounded", False):
            grounded.append(tid)
        else:
            sketch.append(tid)
    runnable = not sketch
    note = (
        "一連に接地（全 T がナレッジ＋作動を備える）→ 稼働可能（TrueNet）"
        if runnable
        else f"未接地（写生 {len(sketch)} T: {', '.join(sketch)}）→ 稼働不可（TrueNet ではない）。"
             "その場でナレッジ（RBP）を埋めて接地する（漸増的実装）。"
    )
    return {
        "runnable": runnable,
        "grounded": grounded,
        "sketch": sketch,
        "actuations": actuations,
        "total": len(trans),
        "grounded_count": len(grounded),
        "note": note,
    }


def net_to_json() -> dict:
    """NET を視覚化向け JSON にする（→ GET /api/net → designer）。

    プレース（丸）・トランジション（縦棒）・弧（pre/post）・複合・作動・
    発火ルール（enabled_when）をそのまま返す。handler は文字列のままだが
    視覚化に不要なモジュールパスを含むためそのまま残す（トレース用）。
    """
    return {
        "name": NET["name"],
        "model": NET["model"],
        "firing": NET["firing"],
        "universal_transition": NET["universal_transition"],
        "places": NET["places"],
        "transitions": NET["transitions"],
        "edges": NET["edges"],
        "entry": NET["entry"],
        "ends": NET["ends"],
        "composite": NET["composite"],
        "runnability": net_runnability(),
    }
