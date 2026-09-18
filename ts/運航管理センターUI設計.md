# 業務運航管理センター UI 設計

**最終更新**: 2026-09-01
**位置づけ**: 真界（TrueWorld / TW）の UI レイヤー設計。OS コア（humos_os）と STN の上に載る「業務運航管理センター」のパネル（UI）を、航空管制塔・鉄道運行管理センターの運行管理室をモデルに設計する。

---

## 1. 目的・背景

### 1.1 監視対象

本 UI は「業務運航管理センター」という**監視業務部隊**が、管制塔・鉄道運行管理室のように
**パネル（UI）を見ながら業務を運用する**ための装置である。

監視対象はサーバーでも工程でもなく:

```
計画時刻 → 期待状態 → 実状態
```

異常の定義:

```
Expected State(T) ≠ Actual State(T)
```

### 1.2 適用ドメイン（同型性）

本 UI はドメイン無依。以下が完全に同型である:

| モデル要素 | 農業(STB) | DC保守 | 航空 | 鉄道 |
|---|---|---|---|---|
| Objective | 病害虫防除完了 | 期限内交換完了 | 定刻運航 | 定刻運行 |
| Milestone | 散布予定日 | 交換期限日 | 離陸時刻 | 発車時刻 |
| State | 薬剤確保済 | 業者手配済 | 搭乗完了 | 出発可能 |
| Artifact | 散布記録 | 交換報告書 | 運航記録 | 運行記録 |
| Actor | 作業者 | 保守業者 | 航空会社・管制 | 鉄道会社・指令 |

### 1.3 既存資産との関係

| 資産 | 役割 | 本 UI との関係 |
|---|---|---|
| `langgraph_designer.html` | STN の**設計者**向け（ネットを作る・HUMOS ログを見る） | 別ページ。同じデータ、別ユーザー |
| 本 UI（`operations.html`） | **運用チーム**向け（計画対比で監視） | 新規作成 |
| `/api/humos` | marking / firing_log / sos_log / replay_ok | **実状態（Actual）の源** |
| `/api/spray_schedule`（STB） | 防除カレンダー（予定日） | **計画（Plan）の源**（STB 固有） |
| 業務定義 DB（新設） | Expected State(T) の正 | **Plan レイヤーの正** |

---

## 2. 三層構造（OS 側の設計前提）

本 UI は 3 つのレイヤーを突き合わせて描画する:

| レイヤー | 担うもの | 実装 | 状態 |
|---|---|---|---|
| **HUMOS** | 実状態 Actual State(T) | マーキング（M） | ✓ 実装済み |
| **STN** | 状態遷移 Transition | 発火ループ（stn.py） | ✓ 実装済み |
| **PLAN** | 期待状態 Expected State(T) | 業務定義 DB（`schedule_def`） | **新設** |

PLAN レイヤーは OS（humos_os）に新モジュール `plan` として追加する。
ドメイン無依（plan, actual, now の 3 入力のみで動作）。

> **実装状況（2026-09-01）**: `humos_os/plan.py` **実装済み**（`schedule_def` DDL・
> CRUD・`expected_state_at()`・`summarize()`）。単体テスト 36/36 成功
> （`tests/test_plan.py`）。STB・DC の両 venv に個別インストール済み。

### 2.1 業務定義 DB（`schedule_def`）

```sql
CREATE TABLE IF NOT EXISTS schedule_def (
    def_id          TEXT PRIMARY KEY,
    net_id          TEXT NOT NULL,          -- どの STN（ドメイン）
    group_id        TEXT,                   -- NULL=独立、同一値=同じプロジェクト（複数マイルストーンを束ねる）
    group_name      TEXT,                   -- プロジェクト名（「SaaS EDI 導入」等）
    name            TEXT NOT NULL,           -- 業務名（散布 / 油交換 / 離陸 / 疎通テスト ...）
    target_key      TEXT NOT NULL,           -- 対象キー名（圃場ID / 発電機ID / 便ID / project）
    target_value    TEXT,                    -- 対象値（NULL=全対象）
    seq             INTEGER DEFAULT 1,       -- マイルストーン順序（group 内 1 始まり・reached 判定用）
    cadence         TEXT NOT NULL
                    CHECK(cadence IN (
                        'daily','weekly','monthly','annual',
                        'interval','event','oneshot'
                    )),
    recurrence      TEXT DEFAULT '{}',       -- JSON: 繰り返し規則（cadence 別）
    deadline_spec   TEXT NOT NULL,           -- JSON: 「いつまでに」（期待時刻 E）
    expected_state  TEXT NOT NULL,           -- 期待状態（STN の place_id）
    grace_seconds   INTEGER DEFAULT 0,       -- 猶予（秒）: 期限 D = E + grace
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
```

> **`group_id` の役割**: 1 つの業務が**複数マイルストーン**で構成される場合
> （SaaS 導入・プロジェクト・導入テスト連番など）に、それらを 1 つのプロジェクトとして
> 束ねる。同一 `group_id` の行は運行図表で **1 本の線（プロジェクト）に N 個の駅**として描画され、
> アラートもプロジェクト単位で集約される。`group_id=NULL` は独立した単発業務。

#### cadence → recurrence / deadline_spec 対応表

| 種別 | cadence | recurrence | deadline_spec |
|---|---|---|---|
| 日次 | `daily` | `{}` | `{"time":"08:50"}` |
| 週次 | `weekly` | `{"dow":1}` | `{"time":"18:00"}` |
| 月次 | `monthly` | `{"dom":1}` | `{"time":"12:00"}` |
| 年次 | `annual` | `{"md":"03-15"}` | `{"time":"12:00"}` |
| 定期 | `interval` | `{"every":"7d"}` | `{"offset":"24h"}` |
| 随時 | `event` | `{"on":"trigger"}` | `{"offset":"2h"}` |
| 臨時 | `oneshot` | `{"at":"2026-09-10T18:00"}` | —（= at） |

### 2.1b 複数マイルストーン業務の登録（プロジェクト）

#### 用語: 定常業務 vs プロジェクト（cadence と group_id の役割分担）

運航管理の文脈では、業務は根本的に **2 種類**に分かれる。この分類が cadence と group_id の
役割分担を定める。

| | **定常業務（運用）** | **プロジェクト** |
|---|---|---|
| 性質 | 繰り返し・定常 | 一回きり・有界（start→end） |
| 周期 | 日次/週次/月次/年次 | **周期なし**（各マイルストーンが oneshot） |
| 終端 | ない（続く） | **ある**（go-live / 完了） |
| モデル上の表現 | cadence 1 行 | **group_id で束ねた oneshot N 行** |
| 例 | 散布・油交換・離陸 | SaaS EDI 導入 |

- **定常業務** = cadence（日次/週次/…）で表す。繰り返しが本質。
- **プロジェクト** = cadence で表さない。`group_id`（＝プロジェクト）で束ねた oneshot の鎖。
  呼び名は「導入業務」「開発/導入業務」などだが、**モデル上の箱は「プロジェクト」**。
  Objective（業務目標）= 終端マイルストーン（go-live）。

> **「不定期」は cadence の種ではなく、定常業務の性質の形容**。
> 防除（病害虫散布）は「日次でも週次でもない」が、それは**プロジェクトではなく定常業務**である。
> 理由は **終端がない**（圃場の防除は続く・go-live という一回きりの終端がない）。
> したがって cadence は `interval`（定期・間隔）または `event`（随時・トリガー）で表し、
> 「不定期」を cadence の値にはしない。
> - **防除 = 定常業務**（`interval` / `event`）。繰り返しが本質・終端なし。
> - **SaaS 導入 = プロジェクト**（oneshot × N ＋ group_id）。一回きり・終端あり。
>
> 区別の一問: **「この業務はいつ『終わり』になるか」**。
> 終わりがない → 定常業務（cadence）。一回きりの終わり（go-live）がある → プロジェクト（group_id）。

#### 登録方法

「日次でも週次でも臨時でもない」業務（SaaS 導入・プロジェクト・導入テスト連番など）は、
**各マイルストーンを `oneshot` として個別登録し、`group_id` で束ねる**ことで扱う。

- **構造（どの状態がどの状態の次か）は STN が担う**（place を鎖状に繋ぐ）。
- **`schedule_def` は各状態に「いつまでに」を付けるだけ**（各マイルストーン 1 行）。
- 1 行 1 マイルストーン。1 プロジェクト = N 行（同一 `group_id`）。

#### 具体例: SaaS EDI 導入

STN 構造: `P_edi_setup → P_retail_test → P_order_test → P_conversion_test → P_wholesale_test → P_go_live`

| # | name | cadence | deadline | expected_state |
|---|---|---|---|---|
| 1 | SaaS EDI 設定 | `oneshot` | 9/15 18:00 | P_edi_setup |
| 2 | 小売疎通テスト | `oneshot` | 9/20 18:00 | P_retail_test |
| 3 | EDI 受注テスト | `oneshot` | 9/25 18:00 | P_order_test |
| 4 | 変換テスト | `oneshot` | 9/30 18:00 | P_conversion_test |
| 5 | 卸テスト | `oneshot` | 10/05 18:00 | P_wholesale_test |
| 6 | サービスイン | `oneshot` | 10/10 18:00 | P_go_live |

全て `group_id='g-edi-001'` / `group_name='SaaS EDI 導入'` / `target_key='project'` / `target_value='edi-001'`。

**運行図表での表示**: 1 本の線（プロジェクト）に 6 個の駅が並ぶ。
**どの駅で止まっているか・予定線からどれだけズレているか**が一目で分かる。
**アラート**: 「SaaS EDI 導入」の 3 番目の駅（EDI 受注テスト）が期限超過、とプロジェクト単位で表示。

> **原則**: 業務を「1 つの cadence に押し込もう」としない。
> 業務は**状態の鎖（STN）＋ 各状態の期限（schedule_def）＋ 束ね（group_id）**で表現する。
> cadence が表すのは「この状態が繰り返し来る周期」であり、プロジェクトの構造ではない。

### 2.2 Expected State(T) の計算（OS が担う）

```python
# humos_os/plan.py（実装済み）

def expected_state_at(
    conn, net_id: str, now: datetime = None, actual_state: str = None
) -> list[dict]:
    """時刻 now における全業務定義の期待状態・逸脱判定を返す。

    actual_state: 現在の状態（STN の place_id）。呼び出し側（server）が
                  マーキング＋STN 構造から導出して渡す。None なら未到達扱い。

    Returns:
        [{def_id, name, group_id, group_name, target_key, target_value, seq,
          cadence, expected_state, actual_state, reached, status,
          expected_at, deadline, slack_seconds, overdue_seconds}, ...]
    """
```

**判定ロジック**（実装準拠）:

1. 各 `schedule_def` の**現行の期待時刻 E**（current occurrence）を計算:
   - oneshot: 固定 `at`
   - daily/weekly/monthly/annual: 現行期間内の予定時刻
   - interval/event: `reference`（アンカー）＋ `offset`
2. **期限 D = E + grace_seconds**（grace は期待時刻と硬期限の猶予）
3. **reached** 判定: `actual_state` の `seq` が `expected_state` の `seq` 以上
   （マイルストーン順序で「到達・通過」したか）
4. 逸脱判定:
   - reached → **`on_track`**（●正常）
   - `now < E`（未達期）→ **`on_track`**
   - `E ≤ now < D`（期待超過・猶予内）→ **`deviating`**（▲潜在異常）
   - `now ≥ D`（硬期限超過）→ **`overdue`**（■遅延/異常）
   - 期限未定（interval/event で reference 未設定）→ **`undefined`**
5. `slack_seconds = D - now`（負なら超過）・`overdue_seconds = max(0, now - D)`

### 2.3 既存防除カレンダーとの接続

STB の防除カレンダー（`/api/spray_schedule`）は `schedule_def` の**特殊ケース**:

```
cadence = interval（防除暦 cron・間隔）または event（病害トリガー・随時）
expected_state = P_inv_result（散布完了）
deadline = 散布予定日
target_key = field_id
```

既存の「予定日」が `deadline` として昇格する。DC の油交換スケジュールも同様（`interval`）。

> **防除は「不定期」だが定常業務**（§2.1b 参照）。cadence の値には「不定期」を作らず、
> `interval`（防除暦 cron）/ `event`（病害トリガー）に落とす。

### 2.4 登録 UI（業務の洗い出し・登録）

「どの暦に登録するのか」への答え: **暦は 1 つ。`schedule_def` の 1 枚の表**。
cadence は「別々の暦」ではなく、その表の**項目（フィールド）**。
日次業務の暦・週次業務の暦… という別々の暦は存在しない。
全部が `schedule_def` に `cadence` 列で区別されて入る。

#### cadence 別登録画面（洗い出しの枠）

**cadence ごとに登録画面を分ける**のが、属人化業務を洗い出すのに自然。
画面は cadence ごとに分ける（洗い出しが楽だから）、**保存先は 1 枚**（モデルの一貫性のため）。

| 登録画面 | cadence | 洗い出すもの |
|---|---|---|
| **日次業務登録** | `daily` | 毎日やること（08:50 までに巡回点検 …） |
| **週次業務登録** | `weekly` | 毎週やること |
| **月次業務登録** | `monthly` | 毎月やること |
| **定期業務登録** | `interval` | 間隔でやること（**防除**・油交換 …） |
| **随時業務登録** | `event` | トリガーでやること（病害発生で散布 …） |
| **臨時・プロジェクト登録** | `oneshot` / group | 一回きり・マイルストーン鎖（SaaS 導入 …） |

#### 登録 UI は「知識の引き出し」装置

登録 UI はデータ入力ではなく、**現場の属人化業務（暗黙知）を `schedule_def` に落とす
知識の引き出し（elicitation）装置**。

```
現場の属人化業務（頭の中・暗黙知）
        │
        ▼  「この圃場の日次業務を洗い出してください」
   [日次業務登録] 画面で列挙 → 各行を登録
   [週次業務登録] 画面で列挙 → 各行を登録
   [定期業務登録] 画面で列挙 → 防除・油交換を登録
   ...
        │
        ▼
   schedule_def（1枚の表）→ 運行図表に線として現れる
```

現場の人が「毎日朝にこれをやってる」「2 週間に 1 回あれする」と**口に出して列挙する行為**が、
暗黙知を `schedule_def` に落とす。cadence ごとに画面を分けるのは、
**「まず日次から洗い出そう」という思考の枠を与える**ため。

#### 登録フォーム（1 業務分）

| 項目 | 例 | 行く列 |
|---|---|---|
| 業務名 | 圃場巡回点検 | `name` |
| 対象 | 圃場 A-1 | `target_key` / `target_value` |
| 期待状態 | 点検済 | `expected_state`（STN の place） |
| いつまでに | 08:50 | `deadline_spec` |
| （cadence） | 日次（画面で確定） | `cadence=daily` |

プロジェクト登録は、`group_name`＋マイルストーン一覧（各: 業務名・期待状態・期限）を入力。

---

## 3. UI レイアウト

### 3.1 全体構成

```
┌──────────────────────────────────────────────────────────────────────────┐
│  業務運航管理センター   [STB: 病害虫防除]    現在: 2026-09-01 08:50       │
│  概要: [● 3 正常] [▲ 1 逸脱] [■ 1 遅延]                                  │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ① 運行図表（線路図）← 中心・壁                                          │
│                                                                          │
├──────────────────────────────────────────────────────────────────────────┤
│  ② ペトリネット・ライブ（機構）  │  ③ 異常解釈（AI 解読層）              │
│     選択中のインスタンスの        │     潜在異常（AI が繋いだ点）          │
│     P/T/トークンを発火アニメで    │     各異常の自然言語説明              │
│     表示。enabled=発光            │     予測・ボトルネック・提案          │
├──────────────────────────────────────────────────────────────────────────┤
│  ④ アラートフィード（時系列・ストリーミング）                             │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3.2 ① 運行図表（線路図）— 中心パネル

**メタファー**: 鉄道運行管理の「運行図表」。縦軸=駅（状態）・横軸=時間。
各業務インスタンス（圃場/発電機/便）を 1 本の線として描く。

```
状態
S6 完了      ────────────────────────────────────────────────────
S5 確認済     ╲
S4 散布済      ╲  · · · · · · · · · · · · · · · · · · · · · · · ·   ← 予定線（破線）
S3 散布前       ╲        ╲
S2 薬剤確保      ╲          ╲  ← 実際線（実線）
S1 評価中        ╲            ╲
                 ╲              ╲
                 └────────────────┴────────────────────────────→ 時間
                 t0            現在 T                    期限
                               │
                     予定: S4（散布済）
                     実際: S2（薬剤確保）
                     逸脱: 2 段階遅れ・期限まで 2 日
```

**データ源**:

| 要素 | 源 |
|---|---|
| 縦軸（状態） | STN の places（状態） |
| 横軸（時間） | 発火ログの `ts`（各状態到達時刻） |
| 実際線（実線） | `firing_log` のタイムスタンプから各状態の到達時刻を抽出 |
| 予定線（破線） | `schedule_def` の `deadline` から期待時刻を計算 |
| 現在線（縦線） | `now` |
| 期限線（赤縦線） | `schedule_def` の `deadline` |

**複数インスタンス = 複数本の線**（複数列車）。1 枚の図に全圃場/全発電機が乗り、
どの線がズレているか・どこで止まっているかが一目で分かる。

**描画ルール**:

- 実際線: 実線・太さ 2px・色=インスタンス別（最大 8 色で循環）
- 予定線: 破線・太さ 1px・灰色
- 現在線: 縦実線・太さ 1px・白・現在時刻ラベル
- 期限線: 縦実線・太さ 1px・赤
- 逸脱（実際線が予定線より右にズレている区間）: 背景を薄赤でハイライト
- 正常（実際線が予定線と一致）: 背景なし
- 線先端（現在位置）: 丸マーカー・色=逸脱状態（緑/黄/赤）

**インタラクション**:

- 線（インスタンス）クリック → ② ペトリネット詳細に切り替え
- ホバー → 現在状態・期待状態・期限・スラックのツールチップ
- 縦軸スクロール → 状態が長い場合（S1〜S10 以上）
- 横軸ズーム → 1 日 / 1 週 / 1 月 / 1 年

### 3.3 ② ペトリネット・ライブビュー（機構）

STN（P/T/トークン）を**ライブグラフ**で表示。ペトリネットの慣例（P=円・T=矩形・トークン=点）をそのまま使う。

```
  [認知]──●──[評価]──●──[決定]──●──[投射]──○──[作動]──○
   P_input  P_sel    P_rx    P_report  P_inv
    ●        ●       ●       ○         ○
                          ▲
                     enabled=発光
                     （発火可能だが待機中）
```

**表示要素**:

| 要素 | 表示 |
|---|---|
| Place（P） | 円。内側にトークンがあれば点（色=状態種別） |
| Transition（T） | 矩形。enabled なら発光（accent 色）、disabled なら暗色 |
| トークン | 点。色=種別（cog/eval/decide/proj/exec） |
| 弧（pre→T→post） | 矢印（純粋なペトリネット表示） |
| 発火アニメ | トークンが P→T→P に移動（300ms） |
| ボトルネック | enabled だが進めない T（後続の pre が未達）= 赤枠 |

**データ源**:

- `/api/net`（STN 構造: places / transitions / edges）
- `/api/humos`（marking: 現在のトークン配置）
- `firing_log`（発火履歴→アニメの起点）

**インタラクション**:

- ① 運行図表の線をクリック → このビューにそのインスタンスの STN が表示
- T クリック → 発火ログの詳細（consumed / generated / ts）
- 自動更新: 5 秒ポーリング（`/api/humos`）

### 3.4 ③ 異常解釈（AI 解読層）

**役割**: 決定論的な Expected/Actual 比較の上に載る「解読層」。
赤い旗「▲逸脱」は警告であってインスピレーションではない。
AI が 5 つの層で異常を「翻訳」する。

#### 5 層の異常表現

| 層 | 見せること | 例 |
|---|---|---|
| **① 記述**（自然言語） | コードでなく文章で | 「B-2 は投射段階で停止中。決定は完了だが Slack 送信（作動）が未完了」 |
| **② 因果**（なぜ） | 発火ログを遡り詰まり点を特定 | 「[投射] が発火待ち → 作動の前置き未達」 |
| **③ 予測**（先） | 現在速度から期限到達を予測 | 「このままでは 9/12 到達・期限 9/10 を 2 日超過の見込み」 |
| **④ パターン**（横断） | **複数インスタンスを繋ぐ** | 「A-1・B-2・C-3 が同時に薬剤確保に集中→供給側ボトルネック」 |
| **⑤ 行動**（提案） | 具体的次の一手 | 「代替薬剤 X は在庫 12。切替で 9/10 に間に合う」 |

**特に ④「パターン」がインスピレーションの核心**:
単一圃場を見ていると「B-2 が遅い」だけ。
AI が横断して「3 圃場が同時に同じ状態に留まっている」と繋ぐと、
**個人の遅延ではなく系統的な制約**（薬剤供給）が見えてくる。
人の目が 1 行では見ない点を、AI が繋いで「あ、そういうことか」と思わせる。

#### AI 異常解釈パネル（表示例）

```
┌ 潜在異常（AI 検知）────────────────────────────────────────────────┐
│ ◆ 薬剤供給ボトルネックの兆候            [新規] 08:52               │
│                                                                    │
│   A-1・B-2・C-3 の 3 圃場が同時期に「薬剤確保(S2)」に              │
│   集中しています。個別には軽微ですが、横断すると薬剤                │
│   在庫・供給側の制約が疑われます。                                  │
│                                                                    │
│   ▸ 文脈: 在庫 薬剤Y=2 / 需要5 ・ 直近 3 日 降雨                  │
│   ▸ 予測: 供給が解消されない場合、3 圃場とも 9/10 超過            │
│   ▸ 提案: 代替薬剤 X（在庫 12）への切替 → 3 圃場とも期限到達      │
│                                                                    │
│   [詳細] [薬剤 X で切替を指示] [在庫を表示]                        │
└────────────────────────────────────────────────────────────────────┘
```

#### アーキテクチャ（AI の接合）

```
plan(DB) + actual(HUMOS marking) + firing_log(ts) + sos_log
        │
        ▼
[決定論] 逸脱判定（Expected(T) vs Actual(T)・スラック計算）  ← OS (humos_os.plan)
        │
        ▼
[生成 AI] 記述・因果・予測・パターン・提案 を自然言語で生成    ← 解読層
        │
        ▼
   UI パネル（③ 異常解釈 / ④ フィード）
```

**AI 呼び出しの設計**:

- 呼び出しタイミング: 逸脱状態が変化した時（ポーリングで検出）
- プロンプトに含める情報:
  - 逸脱している全インスタンスの（expected, actual, deadline, slack）
  - 発火ログの直近 N 件
  - SOS ログの直近 N 件
  - 在庫・外部コンテキスト（ドメイン固有・任意）
- 出力形式: 構造化 JSON（`{title, description, cause, prediction, pattern, action, confidence}`）
- 頻度制限: 同一異常の再解釈は 5 分間隔（コスト抑制）
- 既存 AI（anthropic / gemini / google-genai）を呼ぶ。OS は決定論的な比較まで担い、解釈は AI。

### 3.5 ④ アラートフィード（時系列・ストリーミング）

```
08:52 ▲ B-2: 期待=散布済 実=薬剤確保（期限 9/10 まで 9 日）
08:45 ■ C-3: 期限 9/08 超過・状態=評価中
08:40 ● A-1: Slack 送信完了（SOS 作動記録）
08:30 ● A-1: 投射完了
08:25 ● A-1: 決定完了
```

- 時系列（新着上位）
- 色: ●正常（緑）/ ▲逸脱（黄）/ ■遅延・異常（赤）
- 自動更新: 5 秒ポーリング
- クリック → 該当インスタンスの ① 運行図表にズーム

### 3.6 期限までの余裕（時間制約の可視化）

各業務インスタンスに「期限までの余裕（slack）」を表示する。まだ逸脱していないが、
このままでは期限に届かない＝**潜在異常**を、余裕の減衰として**先回り**で表示できる。

- **余裕（slack）**: 各インスタンスに「残り余裕 X 日」を表示（① 運行図表）
- **余裕消滅**: 余裕を使い切った瞬間（期限超過）、**赤に反転**（＝逸脱・遅延）

**余裕計算**（`humos_os.plan.expected_state_at` が算出）:

```
slack_seconds = deadline - now          # 負なら超過
overdue_seconds = max(0, now - deadline)
```

逸脱状態は `expected_state_at` が `on_track` / `deviating`（grace 内）/ `overdue`（期限超過）
/ `undefined`（期待時刻未定）で判定し、① 運行図表の線色・④ フィード・③ 異常解釈に反映する。

---

## 4. データ契約（API）

### 4.1 新規 API

> **実装状況（2026-09-02）**: `status` / `alerts` / `timeline` / `defs`（CRUD）／
> `ai-interpret` **全て実装済み**（`server.py`・venv で検証済み）。
> `ai-interpret` は 5 層 JSON（記述・因果・予測・パターン・提案）を生成 AI（Anthropic →
> ローカル LLM）で返し、AI 未設定時は決定論フォールバック（単一カード＋横断パターン）で
> 5 層を機械生成。5 分 TTL の頻度制限付き。

| エンドポイント | 方法 | 返却 | 用途 | 状態 |
|---|---|---|---|---|
| `/api/operations/status` | GET | `now` / `actual_state` / `instances[]`（expected, actual, deadline, slack, status）/ `summary` | ① 運行図表・④ フィード | ✅ |
| `/api/operations/alerts` | GET | 逸脱中（deviating/overdue）一覧（優先度ソート） | ③ 異常解釈（決定論的部分） | ✅ |
| `/api/operations/timeline` | GET | `firing_log`（発火 ts）/ `sos_log`（作動 ts）/ `marking` | ① 運行図表の実際線 | ✅ |
| `/api/operations/defs` | GET | 業務定義（schedule_def）一覧（`?group_id=` でフィルタ） | 登録 UI（§2.4） | ✅ |
| `/api/operations/defs` | POST | 業務定義を 1 行登録 | 登録 UI（§2.4） | ✅ |
| `/api/operations/defs/delete` | POST | 業務定義を削除 | 登録 UI（§2.4） | ✅ |
| `/api/operations/ai-interpret` | POST | `{alerts, context}` → `{source, interpretations[]}`（5 層 JSON） | ③ 異常解釈（AI 呼び出し） | ✅ |

**`actual_state` の導出**: server がマーキング（`humos.get_marking()`）から「現在到達している
place」を導出（後段 store≠humos の最後の到達プレース）。`expected_state_at` の `actual_state`
引数として渡す（PLAN は STN を import しない＝不変条件の対称）。

### 4.2 既存 API の再利用

| エンドポイント | 用途 |
|---|---|
| `/api/humos` | marking（実状態）・firing_log（発火履歴）・sos_log（作動記録） |
| `/api/net` | STN 構造（places / transitions / edges）→ ② ペトリネット |
| `/api/spray_schedule`（STB） | 防除カレンダー → `schedule_def` の初期データ |
| `/api/inventory`（STB） | 在庫 → AI 解釈の文脈 |

### 4.3 `/api/operations/status` 返却例

```json
{
  "now": "2026-09-01T08:50:00",
  "instances": [
    {
      "target_value": "A-1",
      "expected_state": "P_inv_result",
      "actual_state": "P_inv_result",
      "deadline": "2026-09-10T18:00:00",
      "slack_seconds": 777600,
      "status": "on_track"
    },
    {
      "target_value": "B-2",
      "expected_state": "P_inv_result",
      "actual_state": "P_rx",
      "deadline": "2026-09-10T18:00:00",
      "slack_seconds": 777600,
      "status": "deviating"
    },
    {
      "target_value": "C-3",
      "expected_state": "P_inv_result",
      "actual_state": "P_input",
      "deadline": "2026-08-30T18:00:00",
      "slack_seconds": -172800,
      "status": "overdue"
    }
  ]
}
```

---

## 5. 人が使うための工夫

### 5.1 色・形（ヘイズル対応）

| 状態 | 色 | 形 | 意味 |
|---|---|---|---|
| ●正常 | 緑 `#4ecb71` | ● | 期待状態に到達 |
| ▲逸脱 | 黄 `#ffb84c` | ▲ | 期限手前・期待未達 |
| ■遅延/異常 | 赤 `#ff5c6c` | ■ | 期限超過・期待未達 |

色盲対応: 形（●▲■）を必ず併記。色だけでは区別しない。

### 5.2 優先度ソート

アラート・フィードの表示順:

1. 期限超過（■）— 最優先
2. スラック消滅（潜在異常）— 2 番目
3. 逸脱（▲）— 3 番目
4. 正常（●）— 最下位（通常は非表示・フィルタで表示）

### 5.3 1 画面で「何を見るか」を 1 つに

- **壁（遠くから）**: ① 運行図表。全インスタンスのズレが一目で
- **中距離**: ② ペトリネット＋③ AI 解釈
- **近く**: ④ フィード＋詳細クリック

迷わせない。管制塔のレーダー画面と同じ情報密度。

### 5.4 インタラクション

| 操作 | 効果 |
|---|---|
| 線（インスタンス）クリック | ② にそのインスタンスの STN を表示 |
| T（トランジション）クリック | 発火ログ詳細（consumed / generated / ts） |
| アラートクリック | ① に該当インスタンスをズーム |
| ホバー | ツールチップ（現在状態・期待・期限・スラック） |
| 横軸ズーム | 1 日 / 1 週 / 1 月 / 1 年 |
| フィルタ | 状態別（●/▲/■）・圃場別 |

### 5.5 時系列の再生

発火ログのタイムスタンプで「この 1 日、状態がどう動いたか」を**再生**できる。
遅延がいつから始まったか一目で分かる。

- 再生速度: 1x / 10x / 100x
- 再生中、① 運行図表の実際線が時間軸に沿って伸びる
- ② ペトリネットが各発火でアニメーション

### 5.6 音（任意・オフ既定）

- ■異常時のみ通知音（管制室の警報）
- 常時では使わない
- 設定で ON/OFF

### 5.7 既存デザイン言語の継承

`langgraph_designer.html` の CSS 変数をそのまま使う:

```css
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface2: #242836;
  --border: #2e3348;
  --text: #e2e4ed;
  --text-dim: #8b8fa3;
  --accent: #6c8cff;
  --danger: #ff5c6c;
  --success: #4ecb71;
  --warn: #ffb84c;
  --cog: #6c8cff;
  --eval: #4ecb71;
  --decide: #ffb84c;
  --proj: #c084fc;
  --exec: #f472b6;
}
```

トランジション別カラー（cog/eval/decide/proj/exec）を ② ペトリネットビューでそのまま使う。

---

## 6. 技術構成

### 6.1 フロントエンド

- **ファイル**: `operations.html`（新規・単一ファイル・既存 UI と同型）
- **描画**: SVG（運行図表・ペトリネットグラフ）+ DOM（パネル・フィード）
- **更新**: 5 秒ポーリング（`/api/operations/status` + `/api/humos`）
- **アニメ**: CSS transition（トークン移動・発光）+ requestAnimationFrame（再生）
- **依存**: 外部ライブラリなし（既存 UI と同型・stdlib のみ）

### 6.2 バックエンド

- **新規 API handler**: `server.py` に追加
  - `_handle_operations_status()`
  - `_handle_operations_alerts()`
  - `_handle_operations_timeline()`
  - `_handle_operations_ai_interpret()`
- **OS 側**: `humos_os/plan.py`（新モジュール）
  - `expected_state_at(conn, net_id, now)` — 逸脱判定
  - `schedule_def` の CRUD
- **AI 呼び出し**: 既存の anthropic / gemini / google-genai（`.env` の API キー）

### 6.3 データフロー

```
schedule_def(DB) ──┐
                   ├──→ humos_os.plan.expected_state_at() ──→ /api/operations/status
humos.marking(DB) ─┘                                                    │
                                                                        ▼
                                              /api/humos (marking, firing_log, sos_log)
                                                                        │
                                                                        ▼
                                              /api/operations/ai-interpret (POST)
                                                                        │
                                                                        ▼
                                              AI (anthropic/gemini) → 解釈 JSON
                                                                        │
                                                                        ▼
                                              UI パネル ③ 異常解釈
```

---

## 7. 石ロードマップへの接続

| 石 | 内容 | 本 UI との関係 |
|---|---|---|
| 石1 | HUMOS 3 関数（fire / write_sos / enabled） | ✓ 実装済み。実状態の源 |
| 石2 | STN マーキング駆動発火ループ | ✓ 実装済み。遷移の源 |
| 石3 | 在庫リフレックス | ✓ 実装済み。SOS ログの源 |
| **石4** | **SSS（状態空間状態システム）** | **PLAN レイヤー（schedule_def + expected_state_at）を含む** |
| 石5 | IF ポート | 外部系（Slack / 在庫管理）との接合 |
| **石6**（新） | **運航管理センター UI** | **本設計。石4 の PLAN レイヤーの上に載る** |

石4（SSS）に PLAN レイヤー（業務定義 DB + Expected State(T) 計算）を含める。
石6（本 UI）は石4 の上に載る。

---

## 8. 実装順序（推奨）

1. ✅ **`humos_os/plan.py`**（実装済み 2026-09-01）— `schedule_def` DDL + `expected_state_at()` + CRUD。テスト `tests/test_plan.py`（36/36）
2. ✅ **`server.py` に `/api/operations/*` 追加**（実装済み 2026-09-01）— status / alerts / timeline ＋ `schedule_def` の CRUD API（`actual_state` をマーキングから導出）
3. ✅ **`operations.html` 作成**（実装済み）— ① 運行図表（SVG）+ ② ペトリネット（SVG）+ ④ フィード
4. ✅ **登録 UI（§2.4）**（実装済み・簡易版）— 業務登録モーダル（cadence 選択・schedule_def 登録）
5. ✅ **AI 解読層**（実装済み 2026-09-02）— `/api/operations/ai-interpret` + ③ パネル。
   逸脱変化時の自動解読＋手動「AI 解読」ボタン。5 層（記述・因果・予測・パターン・提案）。
6. ✅ **期限までの余裕表示**（実装済み・検証済み）— ① 運行図表にスラック（`deadline - now`）表示
7. ✅ **時系列再生**（実装済み 2026-09-03）— 発火ログの再生アニメーション。`operations.html` に
   再生コントロール（1x/10x/100x）＋① 実際線の時間軸に沿った伸長＋② ペトリネットの発火アニメーション。
   Playwright 検証 14/14・スクリーンショット視覚確認済み。
8. ✅ **DC 接続**（実装済み 2026-09-03）— DC の油交換スケジュールを `schedule_def` として登録。
   `DC/scripts/register_oil_schedule_defs.py`（油交換=定常業務→`interval`、reference=前回交換日・
   offset=交換周期・expected_state=`P_completed`）。本番 `dc.db` に 3 発電機を登録し
   `expected_state_at` の E が DC 自身の `next_exchange_date` と一致。テスト `DC/tests/test_oil_schedule_defs.py`（8/8）。

---

## 9. 未確定事項

| # | 事項 | 判断待ち |
|---|---|---|
| 1 | `schedule_def` の `target_value` は 1 行 1 対象か、NULL で全対象か | 1 行 1 対象（明示）を推奨 |
| 2 | AI 解読の頻度制限（5 分間隔）は十分か | 実運用で調整 |
| 3 | 運行図表の横軸のデフォルト範囲（1 日 / 1 週） | 1 週を推奨 |
| 4 | 音通知は ON 既定か OFF 既定か | OFF 既定を推奨 |
| 5 | 時系列再生は MVP に含めるか | 石6 の後続フェーズに |
| 6 | `schedule_def` の管理 UI（登録・編集）は別途作るか | §2.4 で cadence 別登録画面を設計済み。実装順序の 4 に位置付け |
