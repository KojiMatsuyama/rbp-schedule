# LangGraph 設計書 — Petri Netモデルに基づく並列遷移フレームワーク

> 作成日: 2026-08-17
> 最終更新: 2026-08-18
> 状態: 設計（並列遷移モデルへ移行）
> フレームワーク: Petri Net — 並列遷移・トークン放出・独立エージェント

---

## 1. 設計思想

### 1.1 全体のアーキテクチャ — Petri Netモデル

```
ユーザー入力
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  LangGraph StateGraph — Petri Net並列遷移           │
│                                                     │
│  ① 状態ノード — トークン集約（日程・作物・環境）      │
│  ② 認知ノード — ユーザー入力 → 病害虫ベクトル        │
│  ③ 評価ノード — ベクトル → 評価BOXマッチング         │
│  ④ 決定ノード — RBP行列演算 → 処方結果（薬剤名+数量）│
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  処方結果トークン（JSON）が放出される          │  │
│  │  → 2つの遷移が並列に発火                      │  │
│  └───────────────────────────────────────────────┘  │
│       │                                    │        │
│       ▼                                    ▼        │
│  投射ノード                            在庫チェック  │
│  ┌───────────────────┐              ノード          │
│  │ 薬剤名 → メッセージ│              ┌───────────┐  │
│  │ + スコア内訳      │              │ 在庫DB照会 │  │
│  └───────────────────┘              │ あり/なし  │  │
│       │                             │ 不足分     │  │
│       ▼                             └───────────┘  │
│  実行ノード(Slack)              実行ノード(Slack)   │
│       │                                    │        │
│       └────────────────────────────────────┘        │
│                         │                           │
│                         ▼                           │
│                       END                           │
│                                                     │
│  各ノード:                                            │
│    state_node        — トークン集約・発火判定        │
│    perception_node   — 病害虫ベクトルの構築          │
│    evaluation_node   — 評価BOXへの分類               │
│    decision_node     — RBP行列演算による薬剤選定     │
│    projection_node   — メッセージテンプレートの作成  │
│    execution_node    — Slack送信（投射結果）         │
│    inventory_node    — 在庫チェック（新規）          │
│    inventory_exec    — Slack送信（在庫結果）（新規） │
└─────────────────────────────────────────────────────┘
```

### 1.2 なぜPetri Netモデルなのか

従来のDAG（有向非巡回グラフ）は**単一パイプライン**でした：

```
A → B → C → D → END   （直列のみ）
```

Petri Netモデルでは**並列遷移**が可能です：

```
       ┌→ C ──┐
A → B ┤       ├── END
       └→ D ──┘
```

あなたの設計思想「認知→評価→決定→投射→実行」は、決定ノードの時点で**2つの独立したトランジションに分かれる**べきです：

1. **投射トランジション** — 処方結果を人間 readable なメッセージに変換 → Slack送信
2. **在庫トランジション** — 処方結果の薬剤名+数量で在庫をチェック → Slack送信

これはLangGraphの**本来の使い方**であり、あなたのPetri netビジョンに合致します。

### 1.3 トークン放出（Token Release）

Petri netの核心は「**遷移が満たされるとトークンが放出され、次の遷移をトリガーする**」です。

```
decision_nodeの出力:
{
    "prescription": [
        {"name": "ベルクート", "quantity": 3},
        {"name": "ダコニール1011", "quantity": 2}
    ],
    ...
}

↓ 処方トークンが放出される（JSON）

┌─→ projection_node（薬剤名 → メッセージ）
└─→ inventory_node  （薬剤名+数量 → 在庫チェック）
```

### 1.4 既存コードとの関係

```
移行前                          移行後
─────────────────────────────────────────────────
直列DAG（5ノード）          →  Petri Net並列遷移（7ノード）
decision → projection      →  decision → 分岐 → projection
                                └→ inventory_check
Slack送信は1箇所            →  Slack送信は2箇所（並列独立）
```

---

## 2. RBPの数学的基礎

### 2.1 病害虫ベクトル（10次元バイナリ）

```
Index  Disease/Pest      Japanese
──────────────────────────────────────────
  0    Anthracnose       炭疽病
  1    Gray Mold         灰色かび病
  2    Powdery Mildew    うどんこ病
  3    Spider Mite       ナミハダニ
  4    Cutworm           ハスモンヨトウ
  5    Budworm           オオタバコガ
  6    Thrips            ミカンキイロアザミウマ
  7    Stink Bug         ワタノメイガ（アブラムシ系）
  8    Aphid             アブラムシ
  9    Whitefly          コナジラミ
```

例: `["炭疽病", "アブラムシ"]` → `[1, 0, 0, 0, 0, 0, 0, 0, 1, 0]`

### 2.2 評価BOX（評価BOXマッチング）

評価BOXは「特定の病害虫組み合わせを表す10次元ベクトル＋ラベル」。
ユーザーのベクトルと**正確一致**させて、どのシナリオに分類するかを決定する。

```
評価BOX "EB-01" (炭疽病単独):  [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
評価BOX "EB-02" (アブラムシ単独): [0, 0, 0, 0, 0, 0, 0, 0, 1, 0]
評価BOX "EB-22" (炭疽病+アブラムシ): [1, 0, 0, 0, 0, 0, 0, 0, 1, 0]
```

マッチ結果:
- **MATCH** — 正確一致 → 評価BOX ID を返す
- **UNDEFINED** — 不一致 → 未知のシナリオ（ベクトル直接RBP演算）
- **ERROR** — 複数一致 → 曖昧（エラー扱い）

### 2.3 決定 — RBP行列演算（ミラーID）

評価BOXが決まれば、RBPの6段階ブリッジを通過した薬剤の
**ミラーID（コサイン類似度）**でスコアリングし、
最適な薬剤セットを選定する。

```
effectiveness = mirrorId * 10 + coverageRatio * 5
total = effectiveness + safetyBase(20) + resistanceBase(15)
```

---

## 3. State 定義

```python
class ChatState(TypedDict):
    # === 会話メッセージ（履歴用） ===
    messages: list[dict]          # [{"role": "user", "content": "..."}, ...]

    # === ① 状態ノードの出力 ===
    schedule: str | None          # "2026-08-20T09:00:00"
    crop: str | None              # "きゅうり"
    environment: str | None       # "温室"
    growth_stage: str | None      # "育苗中"
    token_ready: str | None       # "pending" | "ready"

    # === ② 認知ノードの出力 ===
    identified_diseases: list[str]  # ["炭疽病", "アブラムシ"]
    vector: list[int]               # [1, 0, 0, 0, 0, 0, 0, 0, 1, 0]

    # === ③ 評価ノードの出力 ===
    eval_box_id: str | None         # "EB-01" or None
    eval_box_name: str | None       # "炭疽病単独" or None
    eval_status: str | None         # "matched" | "undefined" | "error"

    # === ④ 決定ノードの出力（処方トークン） ===
    prescription: list[dict]        # [{"name": "ベルクート", "quantity": 3}, ...]
    mirror_id: float | None         # 0.95
    effectiveness: float | None     # 45.2
    bridge_trace: str | None        # ブリッジ通過履歴
    excluded_drugs: list[str]       # 除外された薬剤
    excluded_combos: list[str]      # 除外された2剤セット
    status: str | None              # "SUCCESS" | "NO_PESTICIDE_DEFINED" | ...

    # === ⑤ 投射トランジションの出力 ===
    projected_message: str | None   # "今回の防除の薬剤は..."

    # === ⑥ 在庫トランジションの出力（新規） ===
    inventory_check: dict | None    # {"ベルクート": {"stock": 5, "needed": 3, "status": "ok"}, ...}
    inventory_message: str | None   # "【在庫チェック結果】..."

    # === 実行結果 ===
    executed_projection: bool       # 投射Slack送信完了
    executed_inventory: bool        # 在庫Slack送信完了

    # === エラーハンドリング ===
    error: str | None
```

---

## 4. ノード設計

### 4.1 ① 状態ノード — state_node

**役割**: トークン集約・発火判定（Petri netモデル）。

**入力**: 外部から投入されたトークン（schedule, crop, environment, growth_stage）
**出力**: token_ready="ready"（全トークン揃った）

```python
def state_node(state: dict) -> dict:
    token_state = get_token_state()  # agentic_chat/tokens.py
    all_present = all(v is not None for v in token_state["tokens"].values())

    if not all_present:
        return {"token_ready": "pending", ...}

    return {"token_ready": "ready", ...}
```

### 4.2 ② 認知ノード — perception_node

**役割**: ユーザーの入力から病害虫の発生状況を「認知」し、10次元バイナリベクトルを構築。

**入力**: `state["messages"]` の最後の user メッセージ
**出力**: `identified_diseases`, `vector`

```python
def perception_node(state: dict) -> dict:
    user_input = get_last_user_message(state["messages"])

    # 症状辞典で病害虫名を特定
    disease_names = lookup_symptom_dict(user_input)

    if disease_names:
        vector = names_to_vector(disease_names)
    else:
        # 症状辞典で特定できなかった → LLMに推論させる
        vector = llm_guess_vector(user_input)

    return {
        "identified_diseases": disease_names or [],
        "vector": vector,
    }
```

### 4.3 ③ 評価ノード — evaluation_node

**役割**: 認知した病害虫ベクトルを評価BOXにマッチさせ、要求を分類。

**入力**: `state["vector"]`
**出力**: `eval_box_id`, `eval_box_name`, `eval_status`

```python
def evaluation_node(state: dict) -> dict:
    vector = state["vector"]
    match = find_eval_box(vector)

    if match.status == "MATCH":
        return {"eval_box_id": match.eval_box_id, ...}
    elif match.status == "UNDEFINED":
        return {"eval_box_id": None, ...}
    else:
        return {"eval_box_id": None, "error": "複数の評価BOXが一致", ...}
```

### 4.4 ④ 決定ノード — decision_node

**役割**: RBP行列演算を行い、最適な薬剤セットを選定。
**ここで処方トークン（JSON）が放出される**。

**入力**: `state["vector"]`, `state["eval_box_id"]`
**出力**: `prescription`（薬剤名+数量）, `mirror_id`, `effectiveness`, `bridge_trace`

```python
def decision_node(state: dict) -> dict:
    vector = state["vector"]
    eval_box_id = state.get("eval_box_id")

    # RBPエンジン呼び出し（Haskell / Pythonフォールバック）
    result = prescribe(vector, eval_box_id=eval_box_id)

    # 処方結果を構築（数量はデフォルト3個）
    prescription = [
        {"name": p["name"], "quantity": 3}
        for p in result["top_set"]
    ]

    return {
        "prescription": prescription,
        "mirror_id": result["mirror_id"],
        "effectiveness": result["effectiveness"],
        "bridge_trace": format_bridge_trace(result["bridgeTrace"]),
        "excluded_drugs": result["excludedIndividual"],
        "excluded_combos": result["excludedSets"],
        "status": result["status"],
    }
```

### 4.5 ⑤ 投射トランジション — projection_node → execution_node

**役割**: 処方結果を人間 readable なメッセージに変換 → Slack送信。

**入力**: `state["prescription"]`, `state["mirror_id"]`, `state["effectiveness"]`
**出力**: `projected_message`

```python
def projection_node(state: dict) -> dict:
    drugs = state["prescription"]
    drug_names = "、".join(d["name"] for d in drugs)

    message = (
        f"【{state['eval_box_name']}】\n"
        f"今回の防除の薬剤は、{drug_names}、です。\n\n"
        f"【スコア内訳】\n"
        f"ミラーID: {state['mirror_id']:.2f}\n"
        f"有効性スコア: {state['effectiveness']:.1f}\n"
        + "".join(f"- {d['name']}: ミラーID={d['mirrorId']:.2f}\n" for d in drugs)
    )

    return {"projected_message": message}
```

### 4.6 ⑥ 在庫トランジション（新規） — inventory_node → inventory_exec_node

**役割**: 処方結果の薬剤名+数量で在庫をチェック → Slack送信。

**入力**: `state["prescription"]`（薬剤名+数量のJSON）
**出力**: `inventory_check`, `inventory_message`

```python
def inventory_node(state: dict) -> dict:
    """
    在庫チェックノード — 処方結果の薬剤名+数量で在庫を照会。

    Petri net遷移:
      処方トークン（薬剤名+数量JSON）がplaceに投入される
      → 在庫チェックが可能になったら発火

    在庫DB: stb.db（既存）または専用テーブル
    """
    prescription = state["prescription"]

    # 各薬剤の在庫をチェック
    inventory_check = {}
    for drug in prescription:
        name = drug["name"]
        needed = drug["quantity"]

        # 在庫DBから照会（SQLite）
        stock = query_stock_from_db(name)

        if stock is None:
            status = "unknown"
            message = f"{name}: 在庫情報なし"
        elif stock >= needed:
            status = "ok"
            message = f"{name}: 在庫あり（在庫:{stock}, 必要:{needed}）"
        else:
            status = "insufficient"
            message = f"{name}: 在庫不足（在庫:{stock}, 必要:{needed}, 不足:{needed-stock}）"

        inventory_check[name] = {
            "stock": stock,
            "needed": needed,
            "status": status,
            "message": message,
        }

    # メッセージを構築
    lines = ["【在庫チェック結果】"]
    for drug in prescription:
        info = inventory_check[drug["name"]]
        lines.append(info["message"])

    # 不足分があれば強調
    insufficient = [d for d in prescription
                    if inventory_check[d["name"]]["status"] == "insufficient"]
    if insufficient:
        lines.append("")
        lines.append("⚠ 在庫不足の薬剤:")
        for d in insufficient:
            info = inventory_check[d["name"]]
            lines.append(f"  - {d['name']}: 不足{info['needed'] - info['stock']}個")

    return {
        "inventory_check": inventory_check,
        "inventory_message": "\n".join(lines),
    }


def inventory_exec_node(state: dict) -> dict:
    """
    在庫実行ノード — 在庫チェック結果をSlackに送信。

    投射トランジションとは独立して動作。
    """
    message = state.get("inventory_message", "")

    if not message:
        return {"executed_inventory": False}

    result = _send_to_slack(message)

    return {
        "executed_inventory": result.get("success", False),
    }
```

---

## 5. グラフ構築 — Petri Net並列遷移

```python
from langgraph.graph import StateGraph, END

def build_graph() -> StateGraph:
    builder = StateGraph(ChatState)

    # ノード追加（7ノード）
    builder.add_node("state", state_node)
    builder.add_node("perception", perception_node)
    builder.add_node("evaluation", evaluation_node)
    builder.add_node("decision", decision_node)

    # 投射トランジション
    builder.add_node("projection", projection_node)
    builder.add_node("execution", execution_node)

    # 在庫トランジション（新規）
    builder.add_node("inventory", inventory_node)
    builder.add_node("inventory_exec", inventory_exec_node)

    # 直列部
    builder.set_entry_point("state")
    builder.add_edge("state", "perception")
    builder.add_edge("perception", "evaluation")
    builder.add_edge("evaluation", "decision")

    # 分岐 — 処方トークンが放出され、2つの遷移が並列に発火
    builder.add_edge("decision", "projection")
    builder.add_edge("decision", "inventory")

    # 収束 — 両方のトランジションがENDに到達
    builder.add_edge("projection", END)
    builder.add_edge("inventory", "inventory_exec")
    builder.add_edge("inventory_exec", END)

    return builder.compile(checkpointer=MemorySaver())
```

### 遷移図

```
state → perception → evaluation → decision
                                 ├─→ projection → END
                                 └─→ inventory → inventory_exec → END
```

**Petri netのplace（場所）とtransition（遷移）:**

| Place（place） | Transition（遷移） | 説明 |
|----------------|-------------------|------|
| トークン集約済み | state → perception | 全トークンが揃った |
| 病害虫認知済み | perception → evaluation | ベクトル構築完了 |
| 評価BOX確定 | evaluation → decision | マッチング完了 |
| 処方結果確定 | decision → projection | 薬剤選定完了（トークン放出） |
| 処方結果確定 | decision → inventory | 薬剤選定完了（トークン放出） |
| メッセージ生成完了 | projection → END | 投射完了 |
| 在庫チェック完了 | inventory → inventory_exec | 在庫照会完了 |
| 在庫送信完了 | inventory_exec → END | 在庫送信完了 |

---

## 6. トークン放出（Token Release）

### 6.1 処方トークンの構造

```json
{
    "prescription": [
        {"name": "ベルクート", "quantity": 3, "id": "P01"},
        {"name": "ダコニール1011", "quantity": 2, "id": "P02"}
    ],
    "mirror_id": 0.95,
    "effectiveness": 45.2,
    "eval_box_name": "炭疽病単独"
}
```

### 6.2 トークン放出の仕組み

LangGraphのStateは**place**として機能します。decisionノードがstateを更新すると、その状態が2つの遷移（projectionとinventory）の**place**に投入されます。

```
decisionノードの出力:
  prescription → [ベルクート:3, ダコニール:2]

↓ stateが更新される（placeにトークンが投入される）

projectionノード: prescriptionを読み取る（遷移発火）
inventoryノード: prescriptionを読み取る（遷移発火）
```

### 6.3 外部からのトークン投入API

```python
# POST /api/tokens/prescribe — 外部から処方トークンを直接投入
@app.route("/api/tokens/prescribe", methods=["POST"])
def post_prescribe_token():
    """外部システムから処方トークンを直接投入"""
    data = request.json
    # {"prescription": [{"name": "ベルクート", "quantity": 3}]}
    return agentic_chat.run_with_prescription(data["prescription"])
```

---

## 7. 公開API

```python
# agentic_chat/__init__.py

def run(
    message: str,
    *,
    conversation_id: str | None = None,
    is_slack_request: bool = False,
    thread_id: str | None = None,
) -> str:
    """
    認知→評価→決定→投射/在庫（並列）→実行 のフルパイプラインを実行。

    Args:
        message: ユーザーメッセージ
        is_slack_request: Trueなら両方の実行ノードでSlack送信する
        thread_id: 会話履歴のスレッドID

    Returns:
        投射されたメッセージテキスト（Slack送信時も返す）
    """
    tid = thread_id or conversation_id or "default"

    state: ChatState = {初期値}

    config = {"configurable": {"thread_id": tid}}

    # パイプライン実行（並列遷移含む）
    result = _app.invoke(state, config=config)

    return result.get("projected_message") or "エラー: 応答がありません"


def run_with_prescription(prescription: list[dict]) -> dict:
    """
    処方トークンを直接投入して並列遷移を実行。

    外部システム（在庫管理、散布予約等）から直接処方結果を投入する場合に使用。

    Args:
        prescription: [{"name": "ベルクート", "quantity": 3}, ...]

    Returns:
        {"projected_message": "...", "inventory_message": "...", ...}
    """
    tid = str(uuid.uuid4())
    state: ChatState = {
        "messages": [],
        "prescription": prescription,
        ...
    }
    config = {"configurable": {"thread_id": tid}}
    result = _app.invoke(state, config=config)
    return {
        "projected_message": result.get("projected_message"),
        "inventory_message": result.get("inventory_message"),
        "executed_projection": result.get("executed_projection"),
        "executed_inventory": result.get("executed_inventory"),
    }
```

---

## 8. 在庫DB設計（新規）

### 8.1 テーブル構造

```sql
CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pesticide_id TEXT UNIQUE,       -- 農薬ID（P01など）
    pesticide_name TEXT NOT NULL,    -- 薬剤名
    quantity INTEGER NOT NULL,       -- 在庫数量
    unit TEXT DEFAULT '本',          -- 単位（本、袋、L）
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- データ例
INSERT INTO inventory (pesticide_id, pesticide_name, quantity) VALUES
('P01', 'ベルクート', 5),
('P02', 'ダコニール1011', 3),
('P03', 'ストロビー', 0);
```

### 8.2 在庫照会関数

```python
def query_stock_from_db(pesticide_name: str) -> int | None:
    """薬剤名から在庫数を取得"""
    conn = sqlite3.connect(os.path.join(APP_ROOT, "data", "stb.db"))
    cursor = conn.execute(
        "SELECT quantity FROM inventory WHERE pesticide_name = ?",
        (pesticide_name,)
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None
```

---

## 9. 依存パッケージ

```bash
pip install langgraph langgraph-checkpoint
```

- `langgraph`: メインフレームワーク
- `langgraph-checkpoint`: 状態永続化（会話履歴用）

---

## 10. 期待される効果

| 指標 | 移行前（直列DAG） | 移行後（Petri Net） |
|------|------------------|-------------------|
| アーキテクチャ | 単一パイプライン | 並列遷移 |
| 拡張性 | ノード追加は直列のみ | 独立トランジションを追加可能 |
| 在庫管理 | 未実装 | 並列に自動チェック |
| Slack送信 | 1箇所 | 2箇所（独立） |
| トークン放出 | なし | 処方結果がplaceに投入 |
| 行数 | 150行（nodes.py） | 250行（nodes.py + 在庫ノード） |

---

## 11. リスクと軽減策

| リスク | 影響 | 軽減策 |
|--------|------|--------|
| LangGraphの並列遷移の学習コスト | 一時的に開発が遅れる | 単純な分岐から始める |
| 在庫DBの未整備 | 在庫チェックが機能しない | SQLiteで軽量化 |
| 在庫情報の陳腐化 | 間違った在庫判定 | 更新時刻を記録、定期的に再取得 |
| 両方のトランジションが失敗 | 何も送信されない | 片方が失敗してももう片方は送信 |

---

## 12. 今後の拡張（Petri Netの真価）

この設計により、将来的に以下のような拡張が容易になります：

```
                                         ┌→ 投射トランジション
decision ── 処方トークン ──┬─→ 在庫トランジション
                         ├─→ 散布予約トランジション（新規）
                         └─→ 気象判定トランジション（新規）
```

各トランジションは独立して動作し、必要なplace（トークン）が揃えば発火します。
これがPetri netモデルの真価です。

---

## 13. 作業記述フレームワーク：マーキング拡張型ペトリネットの設計方針（正規まとめ）

### 13.1 結論

あなたのモデルは、**マーキング（状態）を "プレース＋作業" の単位で扱う拡張ペトリネット** として成立している。

「薬剤トークンを在庫チェックのプレースに発生させ、状態が変化したらプレースが記録する」という提案は、**マーキングの正しい使い方そのもの**である。

### 13.2 マーキングとは何か（あなたのモデルに対応）

マーキング ＝ プレースに存在するトークンの集合（状態）。

あなたのモデルでは、プレースに置かれるトークンは以下の3種：

| トークン種別 | 意味 | 例 |
|-------------|------|---|
| **薬剤トークン** | 選択された薬剤 | "ベルクート" |
| **在庫状態トークン** | 在庫の有無 | "あり" / "なし" |
| **作業状態トークン** | 次の作業は何か | "在庫チェック待ち" |

これらがマーキングとしてプレースに置かれ、プレースは状態の記録装置として機能する。

つまり：

- **トークン** ＝ 状態の内容
- **プレース** ＝ 状態の記録装置
- **マーキング** ＝ 状態の全体像

### 13.3 あなたの提案をマーキングとして形式化

#### ① 薬剤選定作業（判断）

```
決定: 薬剤A
  ↓ 投射
P_stock_check に 薬剤Aトークンを置く
  ↓ マーキングが変化
```

#### ② 在庫チェック作業（作動）

```
T_check_stock が発火
  ↓ 在庫DB参照
  ├─ 在庫あり → 在庫ありトークンを P_state_log に置く
  └─ 在庫なし → 在庫なしトークンを P_state_log に置く
  ↓ マーキングが変化
```

#### ③ プレースが状態を記録

`P_state_log` のマーキングが、薬剤A と 在庫あり/なし を保持する。

### 13.4 あなたのモデルは「マーキング拡張型ペトリネット」になっている

| 概念 | ペトリネット用語 | あなたのモデルでの対応 |
|------|----------------|---------------------|
| プレース | Place | 状態＋ログ |
| トークン | Token | 状態の内容（薬剤・在庫・作業） |
| マーキング | Marking | 状態の全体像 |
| 作業 | Transition | 判断＋作動（トランジション発火） |

これはペトリネットの構造と完全一致している。

### 13.5 図示（あなたのモデルの正規形）

```
P_select_drug
  ● (薬剤A)
    │
    ▼ T_select
P_stock_check
  ● (薬剤A)
    │
    ▼ T_check_stock
P_state_log
  ● (薬剤A, 在庫あり)
```

マーキングがプレースごとに状態を記録していく。

### 13.6 設計判断の整理

| 判断 | 内容 | ペトリネット整合性 |
|------|------|-----------------|
| 投射 | 薬剤トークンを発生させる | ✓ transition発火でplaceにtoken投入 |
| 作動 | 在庫状態トークンを発生させる | ✓ transition発火でplaceにtoken投入 |
| プレース | マーキングとして状態を記録する | ✓ placeはtokenの集合（状態）を保持 |
| 分岐 | できる | ✓ 1つ以上の出力placeを持つtransition |
| 並列 | できない | ※ LangGraphのStateGraphでは分岐は同時実行されないが、Petri Netの本質的な制約ではない |

> **注記**: LangGraphのStateGraphにおける「並列遷移」は、実際には分岐→収束のDAGです。Petri Netの真の並列実行を実現するには、各トランジションを独立したプロセス/スレッドで実行する必要があります。現在の設計では「概念的な並列」（独立した遷移パス）として扱います。

### 13.7 あなたの理解は完全に正しい

あなたのモデルは、**ペトリネットの本質を正しく抽象化した DAG 型作業モデル**になっています。

- 投射は薬剤トークンを発生させる
- 作動は在庫状態トークンを発生させる
- プレースはマーキングとして状態を記録する
- 分岐はできる
- 並列はできない（LangGraphの制約上）

これはペトリネットの構造と完全一致しています。

---

**最終更新: 2026-08-18**
