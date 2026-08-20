# STB RAG 設計書

> 作成日: 2026-08-17
> 状態: 設計フェーズ

---

## 1. 現状分析

### 1.1 既存の検索機構

| 層 | 方式 | 得意なこと | 苦手なこと |
|----|------|-----------|-----------|
| **構造化検索** | SQL LIKE | 正確な名前一致（「炭疽病」「ベルクート」） | 意味の一致（「葉っぱが黄色い」→ 病害不明） |
| **ベクトル演算** | 10次元バイナリ + 余弦類似度 | RBP処方計算（既知の病害虫組み合わせ） | 自然言語入力、症状ベースの推論 |
| **季節ヒューリスティクス** | マンスルー | 記録のない日の代替 | 具体的な症状への対応不可 |

### 1.2 RAGで解決できるギャップ

```
ユーザー: 「葉っぱが黄色くなってて、裏側に小さい虫がついてる」
  ↓ 現状
  → SQL LIKE: 該当なし → 季節推定にフォールバック → 的外れ可能性
  ↓ RAG導入後
  → セマンティック検索: 「黄変 + 裏側小型昆虫」→ アブラムシを推定
  → 既存ツール: アブラムシに効く薬剤をRBPで検索
  → LLM: 統合して自然な回答を生成
```

**具体的なユースケース:**

| ユースケース | 現状の問題 | RAGで改善 |
|-------------|-----------|----------|
| **症状ベース検索** | 病害虫名を知らないと検索不可 | 「葉が丸まってる」→ 関連病害虫を推定 |
| **有機JAS対応** | 成分名で部分一致しかできない | 「有機で使える殺菌剤」→ 成分・規格でセマンティック検索 |
| **回転散布** | 過去記録のSQL検索は可能だが文脈なし | 「カイアシトールから回して」→ 類似成分を検索 |
| **混用相談** | mixingRestrictionは構造化データ | 「ストロビーと混ぜられる？」→ 文脈的に説明 |
| **経験談検索** | 過去の記録は日付指定のみ | 「去年の夏みたいにしたい」→ 季節類似記録を検索 |

---

## 2. アーキテクチャ設計

### 2.1 ハイブリッドRAG構成

```
┌─────────────────────────────────────────────────────┐
│                   ユーザー質問                       │
│  「葉っぱが黄色くなってるけど…」                      │
└──────────────────┬──────────────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │   質問の前処理      │
         │  ・正規化           │
         │  ・エンティティ抽出  │
         │    (病害虫名など)   │
         └─────────┬──────────┘
                   │
      ┌────────────┼────────────┐
      ▼            ▼            ▼
┌─────────┐ ┌──────────┐ ┌──────────┐
│ SQL LIKE│ │ セマンティック│ │ 症状辞典 │
│ 検索    │ │ 検索(RAG)  │ │ ルックアップ│
│(既存維持)│ │(新設)      │ │(新設)     │
└────┬────┘ └────┬─────┘ └────┬─────┘
     │           │            │
     └───────────┼────────────┘
                 ▼
        ┌────────────────┐
        │   結果の統合     │
        │  ・重複排除      │
        │  ・スコア結合    │
        │  ・上位K件選択   │
        └────────┬───────┘
                 ▼
        ┌────────────────┐
        │  コンテキスト構築│
        │  (システムプロンプトに注入)│
        └────────┬───────┘
                 ▼
        ┌────────────────┐
        │   LLM 回答生成   │
        │  (Claude/ローカル)│
        └────────────────┘
```

### 2.2 設計判断

**なぜハイブリッドか？**
- 既存のSQL LIKE検索は高速で正確（名前一致はセマンティックより優れる）
- RAGは補完的に使い、「意味の一致」をカバー
- 両方を統合することで、精度と速度のバランスを取る

**なぜ全文ベクトルDBではなくFAISSか？**
- データ量が少ない（農薬67 + 病害虫10 + 記録37 ≒ 100ドキュメント）
- 別プロセス不要（ChromaDB/Milvus不採用）
- ファイルベースで永続化容易
- サーバー再起動時に自動再読み込み可能

---

## 3. コンポーネント設計

### 3.1 インデックス構築（Index Builder）

#### 3.1.1 チャンキング戦略

各エンティティを「検索に適したテキストチャンク」に分割:

```
農薬ドキュメント例:
┌─────────────────────────────────────────────────┐
│ ID: P01                                          │
│ 名前: ベルクート                                  │
│ ┌─────────────────────────────────────────────┐ │
│ │ チャンク1 (identity):                        │ │
│ │ "ベルクート - 殺菌剤。有効成分: アゾキシストロ│ │
│ │  ビン。FRACグループ: QoI(11)。炭疽病、うどん │ │
│ │  こ病、灰色かび病に効果。PHI: 1日。毒性: 普  │ │
│ │  通物。最大散布回数: 3回。銅剤と混合不可。"   │ │
│ ├─────────────────────────────────────────────┤ │
│ │ チャンク2 (usage):                           │ │
│ │ "ベルクートの使い方: 炭疽病予防に定期散布。QoI │ │
│ │  系統のため抵抗性管理が重要。 Rotation で他  │ │
│ │  FRACグループと交互に使用。"                  │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

**チャンキング規則:**

| エンティティ | チャンクタイプ | 内容 | 目安 |
|-------------|---------------|------|-----|
| **農薬** | `identity` | 名前+成分+類別+標的+PHI+毒性+散布制限 | 1ドキュメントあたり1-2チャンク |
| **農薬** | `usage` | 使用方法+抵抗性管理+混用情報 | 1ドキュメントあたり1チャンク |
| **病害虫** | `profile` | 名称+種類+特徴症状 | 1ドキュメントあたり1チャンク |
| **病害虫** | `symptoms` | 具体的な症状描述（追加作成） | 1ドキュメントあたり1-2チャンク |
| **記録** | `history` | 日付+発生病害虫+使用薬剤 | 1ドキュメントあたり1チャンク |

**合計チャンク数: 約150-200件**（農薬67×2 + 病害虫10×2 + 記録37）

#### 3.1.2 テキスト生成ロジック

```python
def build_pesticide_chunk(pesticide: dict) -> dict:
    """農薬レコードから検索用テキストチャンクを生成"""
    name = pesticide["name"]
    ingredient = pesticide.get("activeIngredient", "")
    category = pesticide.get("category", "")
    cat_jp = {"fungicide": "殺菌剤", "insecticide": "殺虫剤", "acaricide": "殺ダニ剤"}[category]
    targets = pesticide.get("targetNames", [])
    phi = pesticide.get("phiDays", "?")
    tox = pesticide.get("toxicityClass", "")
    max_app = pesticide.get("maxApplications", "無制限")
    mix_restrict = pesticide.get("mixingRestriction", "")
    system = pesticide.get("system", "")
    frac = pesticide.get("systemCode", "")

    # Identity chunk: 基本情報の自然言語記述
    identity_text = (
        f"{name}は{cat_jp}です。有効成分は{ingredient}。"
        f"FRACグループは{system}({frac})。"
        f"標的病害虫は{', '.join(targets)}。"
        f"PHIは{phi}日。毒性区分は{tox}。"
        f"最大散布回数は{max_app}回。"
        f"{mix_restrict}" if mix_restrict else ""
    )

    # Usage chunk: 使用方法・注意点
    usage_text = (
        f"{name}の使い方: {cat_jp}として{', '.join(targets)}に使用。"
        f"{frac}系統のため、抵抗性管理のため他のFRACグループと回転使用すること。"
        f"{mix_restrict}" if mix_restrict else ""
    )

    return {
        "chunks": [
            {"type": "identity", "text": identity_text, "source_id": name, "source_type": "pesticide"},
            {"type": "usage", "text": usage_text, "source_id": name, "source_type": "pesticide"},
        ]
    }
```

#### 3.1.3 症状辞典（Symptom Dictionary）

セマンティック検索の精度を上げるための「症状→病害虫」マッピング:

```python
SYMPTOM_DICTIONARY = {
    "葉っぱが黄色くなる": ["アブラムシ", "うどんこ病", "栄養障害"],
    "葉っぱに穴が開く": ["ヨトウムシ", "アオムシ"],
    "葉の裏に白い虫": ["コナジラミ", "アブラムシ"],
    "葉が丸まる": ["アブラムシ", "モザイク病"],
    "葉に白い粉": ["うどんこ病"],
    "葉にぬめり": ["灰色かび病"],
    "実が腐る": ["炭疽病", "灰色かび病"],
    "花が落ちる": ["灰色かび病", "温度障害"],
    "茎が柔らかい": ["灰色かび病"],
    "糸状の蜘蛛": ["ナミハダニ", "ハダニ"],
    "葉に銀色の跡": ["アザミウマ"],
    "葉に透明感": ["アザミウマ"],
    "株元が腐る": ["モザイク病", "軟腐病"],
    "葉に輪紋": ["モザイク病"],
}
```

> **設計判断**: これはRAGではなくルックテーブル。セマンティック検索の前にまずここをチェックし、病害虫名が特定できれば既存の強力なSQL検索にフォールバックする。

### 3.2 埋め込みモデル

#### オプション比較

| モデル | サイズ | 依存 | 日本語性能 | 推奨度 |
|--------|--------|------|-----------|--------|
| **all-MiniLM-L6-v2** | 8MB | torch | ★★★☆☆ | ⭐⭐⭐⭐ |
| **jina-embeddings-v3** | - | onnxruntime | ★★★★☆ | ⭐⭐⭐⭐⭐ |
| **SentenceTransformers (ONNX)** |  varied | onnxruntime | ★★★☆☆ | ⭐⭐⭐ |
| **TF-IDF + BM25** | 0 | sklearn | ★★☆☆☆ | ⭐⭐ |

#### 推奨: jina-embeddings-v3（ONNX版）

**理由:**
- PyTorch不要（ONNX Runtimeのみ）
- 日本語に強い（日本語ファインチューニング済み）
- API風に使えてシンプル
- 軽量（メモリ〜100MB）

**ただし**: jinaはAPIキーが必要になる可能性があるため、**オフライン前提なら替代案も用意**

#### 代替案: ONNX対応の軽量マルチ lingual モデル

```python
# pip install onnxruntime sentence-transformers
from sentence_transformers import SentenceTransformer

# ONNX版モデル（PyTorch不要）
model = SentenceTransformer(
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    trust_remote_code=True,
)
# → ONNX Export → onnxruntimeで推論
```

#### 最小依存案: numpy + TF-IDF

```python
# pip install scikit-learn
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np

vectorizer = TfidfVectorizer()
vectors = vectorizer.fit_transform(corpus)  # 全チャンクのTF-IDF

def search(query):
    q = vectorizer.transform([query])
    scores = (vectors @ q.T).toarray().flatten()
    return np.argsort(-scores)[:10]  # トップ10
```

> **設計判断**: まず **TF-IDF + numpy** でPoCを作り、品質が物足りなければ embedding モデルにアップグレードする段階的アプローチを推奨。

### 3.3 ストレージ

#### FAISSインデックス

```
data/rag_index.faiss    # FAISS IVFFlat インデックス
data/rag_meta.json      # メタデータ（chunk_id → source情報）
data/rag_tfidf.npz      # TF-IDFベクトライザー（最小依存案の場合）
```

**インデックス構造:**
```python
class RAGStore:
    """RAGインデックスの読み込み・検索・再生成"""

    def __init__(self, db_path: str, index_path: str):
        self.index_path = index_path
        self.conn = get_db(db_path)

    def rebuild(self):
        """DBから全データをロードしてインデックスを再生成"""
        chunks = self._extract_chunks()  # 全チャンク抽出
        self._build_index(chunks)        # TF-IDF / FAISS構築
        self._save(chunks)               # ディスクに保存

    def load(self) -> bool:
        """ディスクからインデックスを読み込み"""
        return os.path.exists(self.index_path)

    def search(self, query: str, limit: int = 5) -> list:
        """クエリに類似したチャンクを検索"""
        ...
```

### 3.4 MCPツール統合

新しいツールを `mcp_tools.py` に追加:

```python
# TOOL_REGISTRY に追加
{
    "name": "retrieve_context",
    "description": "ユーザーの質問に関連する農薬・病害虫・記録のコンテキストを検索して返す。症状描述や自然言語の質問に対応。",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "検索クエリ（自然言語で記述）"},
            "limit": {"type": "integer", "description": "最大取得件数（デフォルト5）"},
            "source_type": {"type": "string", "description": "ソースタイプで絞り込み（任意: pesticide, disease, record, all）"},
        },
        "required": ["query"],
    },
},
```

**実装:**
```python
def retrieve_context(query: str, limit: int = 5, source_type: str = "all") -> str:
    """
    RAGで関連コンテキストを検索。

    フロー:
    1. 症状辞典で病害虫名を特定尝试（あればSQL検索にフォールバック）
    2. セマンティック検索で類似チャンクを取得
    3. 結果をJSONで返す
    """
    rag = get_rag_store()

    # Step 1: 症状辞典チェック
    matched_diseases = _lookup_symptom_dict(query)
    if matched_diseases:
        # 病害虫名が特定できた → 既存の強力なSQL検索に委譲
        results = []
        for disease in matched_diseases:
            # search_pesticides(disease) を呼ぶ
            results.append(search_pesticides(disease))
        return json.dumps({"method": "symptom_lookup", "results": results}, ensure_ascii=False)

    # Step 2: セマンティック検索
    chunks = rag.search(query, limit=limit, source_type=source_type)
    return json.dumps({
        "method": "semantic_search",
        "query": query,
        "chunks": chunks,
    }, ensure_ascii=False)
```

### 3.5 システムプロンプト統合

`claude_chat.py` の `SYSTEM_PROMPT` に追記:

```
## 🔎 RAG検索 — 質問が曖昧な時は必ず使う

ユーザーの質問が以下のいずれかに当てはまる場合、必ず `retrieve_context` ツールを最初に呼び出してください:

1. **症状描述**: 「葉っぱが黄色い」「丸まってる」「白い粉が吹いてる」など
2. **有機・規格**: 「有機JAS対応」「登録農薬」など
3. **経験ベース**: 「去年みたいに」「前と同じように」など
4. **混用相談**: 「◯◯と混ぜられるか」など

retrieve_context で取得したコンテキストを元に回答してください。
```

---

## 4. 実装フェーズ

### Phase 1: TF-IDF PoC（推定工数: 1-2日）

**ゴール**: 最低限動くRAGを検索可能にする

```
mcp_tools.py
├── RAGStore (TF-IDF版)
│   ├── rebuild()    — DB→チャンク→TF-IDF
│   ├── search()     — クエリ→類似チャンク
│   └── save/load    — ディスク永続化
│
├── SYMPTOM_DICTIONARY — 症状→病害虫マッピング
├── retrieve_context() — 新MCPツール
└── _extract_chunks()  — DB→テキストチャンク変換
```

**変更ファイル:**
- `mcp_tools.py` — RAGStoreクラス + retrieve_contextツール追加
- `claude_chat.py` — SYSTEM_PROMPT追記 + retrieve_contextをtool_mapに追加

**テスト:**
```python
# 手動テストケース
tests = [
    ("葉っぱが黄色くなってる", "アブラムシ関連薬剤"),
    ("有機で使える殺菌剤", "有機JAS対応農薬"),
    ("葉に白い粉っぽいもの", "うどんこ病薬剤"),
    ("カイアシトールと似たもの", "同一成分/類似成分"),
    ("ストロビーと混用できる?", "混用可能薬剤"),
]
```

### Phase 2: 症状辞典の充実（推定工数: 1日）

**ゴール**: 症状ベース検索の精度向上

- 症状パターンを50件以上に拡充
- 複数症状の組み合わせ対応（「黄色い + 裏側に虫」）
- 病害虫プロフィールの自動生成（DBから症状記述を構築）

### Phase 3: 埋め込みモデルへの移行（推定工数: 2-3日）

**ゴール**: TF-IDFの限界（同義語・類義語）を克服

```
条件分岐:
  TF-IDF精度が満足できなければ →
    pip install onnxruntime sentence-transformers
    → ONNX埋め込みモデルに切り替え
    → FAISSインデックスに変更
```

### Phase 4: 記録セマンティック検索（推定工数: 1日）

**ゴール**: 過去の防除記録を意味で検索

```
例: 「去年の夏の対策みたいなの」
  → 記録チャンクをセマンティック検索
  → 類似期間の記録を返す
```

---

## 5. 期待される効果

| 指標 | 現状 | Phase 1目標 | Phase 3目標 |
|------|------|------------|------------|
| 症状ベース検索成功率 | ~30% | ~60% | ~80% |
| 有機JAS検索精度 | 部分一致のみ | キーワード+セマンティック | 高精度 |
| 誤検知率 | 低い | 低〜中 | 低 |
| 応答時間 | <100ms | <200ms | <500ms |
| 追加依存 | なし | scikit-learn | +onnxruntime |

---

## 6. リスクと軽減策

| リスク | 影響 | 軽減策 |
|--------|------|--------|
| TF-IDFの日本語性能不足 | 検索精度が低い | Phase 3で埋め込みモデルに移行 |
| システムプロンプトが複雑化 | LLMが混乱する | retrieve_contextの使用条件を明確化 |
| インデックスの陳腐化 | 古いデータで検索 | サーバー起動時に自動rebuild |
| 依存パッケージの増加 | デプロイが複雑に | scikit-learnは軽量。ONNXはオプション |
| 農薬データの正確性 | 間違った推奨 | RAGは参考情報として明示。最終判断はツール結果ベース |

---

## 7. 依存パッケージ追加一覧

```
# Phase 1のみ
scikit-learn        # TF-IDF / BM25

# Phase 3（オプション）
onnxruntime         # ONNX推論エンジン
sentence-transformers  # 埋め込みモデル（ONNX対応版）
faiss-cpu           # FAISS（CPU版）
```

---

## 8. ファイル変更サマリー

```
変更予定ファイル:
├── mcp_tools.py          [+200行] RAGStore, retrieve_context, シンプト辞典
├── claude_chat.py        [+20行]  SYSTEM_PROMPT追記
├── data/rag_index.pkl    [新設]   TF-IDFインデックス（pickle保存）
└── data/symptom_dict.json [新設]  症状辞典（メンテナンス用）
```

---

## 9. 判断待ち事項

| 項目 | 選択肢 | 推奨 | 備考 |
|------|--------|------|------|
| **埋め込み方式** | TF-IDF vs 埋め込みモデル | **TF-IDFでPoC→段階移行** | 低风险、迅速な検証 |
| **インデックス形式** | pickle vs JSON | **pickle** | シリアライズ简单、numpy配列対応 |
| **再インデックス时机** | 起動時 vs 変更時 | **起動時** | 简单実装。変更通知はPhase 2以降 |
| **検索結果の表示** | 生チャンク vs 要約 | **生チャンク（LLMに任せる）** | LLMが文脈に合わせて整形 |

---

**最終更新: 2026-08-17**
