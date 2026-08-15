# RBP（Reflect Block Pattern）設計原理 — 一般形

## 要約

RBPは、「要求評価 → 候補生成 → 制約充満 → 仕様決定」を
行列とベクトルの演算だけで統一する離散構造学習モデルである。

複数条件は行列のゼロ化とHadamard積で表現され、
if/elseはReflect層の前処理に吸収される。

これは農薬防除に限らず、在庫管理・販売管理・権限管理・スケジューリングなど、
あらゆるビジネスロジックを統一できる一般形の仕様決定アルゴリズムである。

---

## 1. 基本記号

| 記号 | 意味 | 次元 |
|------|------|------|
| $\vec{x}$ | EntryVector（要求評価ベクトル） | $\mathbb{R}^d$ |
| $B$ | BridgeMatrix（候補生成行列） | $\mathbb{R}^{n \times d}$ |
| $S$ | SafetyVector（制約ベクトル） | $\mathbb{R}^n$ |
| $C$ | ConstraintMatrix（複合制約行列） | $\mathbb{R}^{n \times k}$ |
| $R$ | ReflectMatrix（制約充満行列） | $\mathbb{R}^{n \times n}$ |
| $\vec{s}$ | SpecVector（最終仕様ベクトル） | $\mathbb{R}^n$ |
| $\odot$ | Hadamard積（要素ごとの積） | — |

---

## 2. 要求評価（Demand）— EntryVectorの生成

### 2.1 定義

$$\vec{x} = E(\sigma) \in \mathbb{R}^d$$

$\sigma$ は対象の状態（病害虫の有無、季節、在庫、役割など）。
$E$ は状態を固定次元 $d$ のベクトルに変換するエンコーディング関数。

### 2.2 性質

- $\vec{x}_i \in \{0, 1\}$ — 離散ベクトル（対象の有無）
- $\|\vec{x}\|_0 = k$ — $k$ 個のラインが「通水」する
- 次元 $d$ はドメインに依存しない抽象度で設計される

### 2.3 例（農薬防除）

$$\vec{x}_{\text{entry}} = [1, 0, 0, 1]^T$$

病害虫1番目と4番目の発生 → ライン1と4が通水

---

## 3. 候補生成（Bridge）— BridgeMatrixの適用

### 3.1 定義

$$\vec{y} = B \cdot \vec{x}$$

$B \in \mathbb{R}^{n \times d}$ は「あみだくじ構造」を行列で表現したもの。
$n$ は候補の数（薬剤数、権限数、在庫品目数など）。

### 3.2 線形結合としての解釈

$$\vec{y} = \sum_{i=1}^{d} x_i \cdot \vec{B}_{\cdot i}$$

$x_i = 1$ の列 $\vec{B}_{\cdot i}$ の成分のみが通過し、
$x_j = 0$ の列 $\vec{B}_{\cdot j}$ の成分は遮断される。

### 3.3 例

$$B = \begin{bmatrix}
A & B \\
C & D
\end{bmatrix}, \quad
\vec{x} = \begin{bmatrix} 1 \\ 0 \\ 0 \\ 1 \end{bmatrix}$$

$$\vec{y} = B \cdot \vec{x} = 1 \cdot \vec{B}_{\cdot 1} + 0 \cdot \vec{B}_{\cdot 2} + 0 \cdot \vec{B}_{\cdot 3} + 1 \cdot \vec{B}_{\cdot 4}$$

→ AとDの成分のみが通過。BとCはゼロ化される。

### 3.4 逆止弁制約

$$B_{ij} \geq 0 \land \forall i,j: B_{ji} = 0 \text{ if } B_{ij} > 0$$

流れは一方通行（forward only）。循環は禁止。

---

## 4. 制約充満（Reflect）— SafetyVectorとHadamard積

### 4.1 SafetyVectorの定義

$$S = [\alpha_1, \alpha_2, \ldots, \alpha_n]^T \in [0, 1]^n$$

各要素 $\alpha_i$ は$i$番目の候補に対する制約の充足度：

| $\alpha_i$ | 意味 |
|-----------|------|
| $0$ | 完全遮断（条件違反） |
| $(0, 1)$ | 減衰（条件_partial_違反、減点対象） |
| $1$ | 完全通過（条件充足） |

### 4.2 一次制約充満

$$\vec{x}' = \vec{x} \odot S$$

$$\vec{x}'_i = x_i \cdot \alpha_i$$

制約違反の要素が自動的にゼロ化される。if/else不要。

### 4.3 複数制約の逐次合成 — Reflect層

$k$ 個の制約条件 $W_1, W_2, \ldots, W_k \in [0, 1]^n$ に対して：

$$\vec{f} = \vec{x} \odot W_1 \odot W_2 \odot \cdots \odot W_k$$

展開すると：

$$\vec{f}_i = x_i \cdot \prod_{j=1}^{k} (W_j)_i$$

### 4.4 ReflectMatrixの定義

$k$ 個の制約重みを対角行列として配置：

$$R = \mathrm{diag}(W_1) \cdot \mathrm{diag}(W_2) \cdot \cdots \cdot \mathrm{diag}(W_k) = \mathrm{diag}\left(\prod_{j=1}^{k} W_j\right)$$

$$\vec{f} = R \cdot \vec{x}$$

$R$ は対角行列であり、Hadamard積 $\odot$ と行列積 $\cdot$ は等価：

$$R \cdot \vec{x} = \vec{x} \odot \mathrm{diag}(R)$$

### 4.5 農薬防除におけるReflect層の具体例

$$\vec{f} = \vec{x} \odot w_{\text{TARGET}} \odot w_{\text{USAGE}} \odot w_{\text{PHI}} \odot w_{\text{ROTATION}} \odot w_{\text{MIXING}} \odot w_{\text{TOXICITY}}$$

各重みの意味：

| 重みベクトル | 制約条件 | 遮断条件 | 減衰条件 |
|------------|---------|---------|---------|
| $w_{\text{TARGET}}$ | ターゲット一致 | $\mathrm{dot}(\vec{x}, \vec{t}) = 0$ → $0$ | — |
| $w_{\text{USAGE}}$ | 散布回数上限 | $u \geq u_{\max}$ → $0$ | — |
| $w_{\text{PHI}}$ | PHI残日数 | — | $\Delta < \phi$ → $0.5$ |
| $w_{\text{ROTATION}}$ | 系統ローテーション | — | $r \geq 2$ → $0.3$ |
| $w_{\text{MIXING}}$ | 混用禁止 | 前回薬剤と衝突 → $0$ | — |
| $w_{\text{TOXICITY}}$ | 毒性区分 | — | 劇物 → $0.7$ |

---

## 5. 仕様決定（Spec）— SpecVectorの抽出

### 5.1 定義

$$\vec{s} = \mathrm{extract}(\vec{f})$$

$\vec{f}$ から非零要素を抽出し、最終仕様を決定する。

### 5.2 抽出方法

**方法A（単純選択）:**

$$\vec{s}_i = \begin{cases} f_i & \text{if } f_i > 0 \\ 0 & \text{otherwise} \end{cases}$$

**方法B（Mirror-IDによる最適化）:**

$$\vec{s} = \arg\max_{\vec{c} \in \mathcal{C}(\vec{f})} \mathrm{cosine}(\mathrm{union}(\vec{c}), \vec{x})$$

$\mathcal{C}(\vec{f})$ は $\vec{f}$ から生成される候補セット集合。
$\mathrm{union}(\vec{c})$ はセット $\vec{c}$ のユニオン被覆ベクトル。

### 5.3 Mirror-ID（意味距離）

$$\mathrm{mirror}(\vec{c}, \vec{x}) = \frac{\mathrm{union}(\vec{c}) \cdot \vec{x}}{\|\mathrm{union}(\vec{c})\| \cdot \|\vec{x}\|}$$

EVAL_BOX（要求の意味構造）とSPEC_BOX（仕様の意味構造）の適合度をコサイン類似度で測定。

---

## 6. 行列化の原理 — if/elseの消去

### 6.1 基本原理

> 条件分岐は行列のゼロ化に変換できる。
> if/elseはReflect層の前処理に吸収される。

### 6.2 変換規則

| if/else表現 | 行列表現 |
|------------|---------|
| `if (usage >= max) exclude` | $w_{\text{USAGE}}[i] = 0$ |
| `if (phi_days > interval) reduce` | $w_{\text{PHI}}[i] = 0.5$ |
| `if (mixing_conflict) exclude` | $w_{\text{MIXING}}[i] = 0$ |
| `if (toxicity == "劇物") penalize` | $w_{\text{TOXICITY}}[i] = 0.7$ |
| `if (rotation_count >= 2) reduce` | $w_{\text{ROTATION}}[i] = 0.3$ |

### 6.3 前処理と本体の分離

```
前処理层（ドメイン固有）:    本体（ドメイン非依存）:
                          ┌─────────────────────┐
usage → w_USAGE           │  f = x ⊙ W₁ ⊙ W₂ ⊙   │
phi → w_PHI               │         ··· ⊙ Wₖ      │
mixing → w_MIXING         └─────────────────────┘
rotation → w_ROTATION
toxicity → w_TOXICITY
```

前処理はif/elseを含むが、本体は純粋なHadamard積のみ。

---

## 7. RBP一般形の数学的定義

### 7.1 制約充満問題としての定義

$$\vec{s} = \underset{\vec{c} \in \mathcal{C}(B \cdot \vec{x})}{\arg\max} \; \mathrm{mirror}(\vec{c}, \vec{x})$$

$$\mathcal{C}(B \cdot \vec{x}) = \{ \vec{c} \mid \vec{c} \subseteq \{i \mid (R \cdot \vec{x})_i > 0\} \}$$

ここで：
- $\mathcal{C}(B \cdot \vec{x})$: Bridgeが生成した候補集合
- $R$: Reflectが定義する制約行列
- $\mathrm{mirror}(\cdot)$: Mirror-ID（適合度関数）

### 7.2 静的モデル vs 動的モデル

**静的モデル（ADのような固定ルール）:**

$$f(\vec{x}) = 0 \quad \forall \vec{x}$$

制約関数は一定。BridgeMatrixのみで完結。

**動的モデル（農薬防除のような履歴依存）:**

$$f(\vec{x}, \rho) \neq 0$$

制約関数は履歴 $\rho$ に依存。SafetyVectorが動的に更新される。

---

## 8. RBP一般形の構造まとめ

| 層 | 役割 | 数学的操作 | 行列構造 |
|----|------|-----------|---------|
| **Demand** | 状態をベクトル化 | $\vec{x} = E(\sigma)$ | EntryVector $\in \mathbb{R}^d$ |
| **Bridge** | 候補生成 | $\vec{y} = B \cdot \vec{x}$ | BridgeMatrix $\in \mathbb{R}^{n \times d}$ |
| **Reflect** | 制約充満 | $\vec{f} = R \cdot \vec{x}$ | ReflectMatrix $\in \mathbb{R}^{n \times n}$ |
| **Spec** | 最終仕様決定 | $\vec{s} = \mathrm{extract}(\vec{f})$ | SpecVector $\in \mathbb{R}^n$ |

---

## 9. 制約条件のDSL表現

### 9.1 全候補到達可能性制約

$$\forall i \in \{1, \ldots, n\}: (R \cdot \vec{x})_i > 0 \Rightarrow \exists j: B_{ij} \cdot x_j > 0$$

すべての候補が少なくとも1つの要求ラインから到達可能。

### 9.2 逆止弁制約

$$\forall i,j: B_{ij} > 0 \Rightarrow B_{ji} = 0$$

流れは一方通行。

### 9.3 循環なし制約

$$\forall \text{ path } p = (v_0, v_1, \ldots, v_m): \mathrm{level}(v_0) < \mathrm{level}(v_1) < \cdots < \mathrm{level}(v_m)$$

BRIDGEのlevelは狭義単調増加。

---

## 10. 最終結論

RBPは、要求評価・候補生成・制約充満・仕様決定を
行列とベクトルの演算だけで統一する離散構造学習モデルである。

複数条件は行列のゼロ化とHadamard積で表現され、
if/elseはReflect層の前処理に吸収される。

これは、農薬防除、在庫管理、販売管理、権限管理、スケジューリングなど
あらゆるビジネスロジックを統一できる一般形の仕様決定アルゴリズムである。

---

## 附録A: 実装マッピング

| 数学記号 | 実装変数名 | ファイル |
|---------|-----------|---------|
| $\vec{x}$ | `ebVector` | 呼び出し側 |
| $B$ | `SPEC_BRIDGES` 配列 | `rbp/spec_bridges.js` |
| $S$ | `safetyVector` | `rbp/safety.js` |
| $W_j$ | `bridge.weight_vector_fn(ctx)` | `rbp/spec_bridges.js` |
| $R$ | `hadamard` 連鎖 | `framework/rbp_core.js` |
| $\vec{s}$ | `best.pesticides` | `rbp/prescription.js` |
| $\mathrm{cosine}$ | `cosineSimilarity` | `framework/engine.js` |
| $\mathrm{union}$ | `computeUnionCoverage` | `framework/mirror.js` |

## 附録B: 不完全な部分

| 数学的理想 | 現状の実装 | 差分 |
|-----------|-----------|------|
| $R$ の独立構造 | Hadamard連鎖で分散 | ConstraintMatrix未独立化 |
| $\vec{s}$ のベクトル抽出 | 配列表現（セット列挙） | 代数的抽出未実装 |
| セット内混用禁止の代数化 | if-elseループ | `prescription.js:162-169` |
| 全処理の代数的統一 | `spec_matching.js`に残存 | if-else方式が併存 |
