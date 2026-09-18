# HUMOS 管理画面（OS コンソール）設計

> 作成: 2026-09-09
> 位置づけ: 真界（TrueWorld / TW）の **OS レベル** の管理画面。ドメイン（STB/DC/将来）に依存しない。
> 上位文書: [真界.md](真界.md)（本質（哲学）＋本質4層・構成全体）／[真界哲学_HUMOS設計.md](真界哲学_HUMOS設計.md)（統一版）
> 実装対象: `humos.html`（新）＋ `server.py`（API 拡張・配信）

---

## 0. 要約（TL;DR）

HUMOS は OS として**どのドメイン開発にも共通**で使われる。ドメイン固有の画面
（index.html の圃場マスター等、operations.html の運航管理）とは別に、**OS そのもの**
を映す管理画面が要る。

- **骨格 = 真界.md の「本質4層」をそのまま**（①本質 ②核心 ③機構 ④OSである所以）。
  OS が自分の設計を自己言及する形にする。
- **ドメイン非依存の実現** = operations.html と同一パターン。`humos.html` を1枚作り、
  各ドメインサーバーが相対配信し、**そのサーバーの humos_os DB を読む**。
  UI コードはドメインに一切依存しない（`domain_id` / `net_id` はサーバーが渡す）。
- **新規 API は1つ**（`/api/humos/meta`）＋ `/api/humos` に `writing_log` を追加するだけ。
  既存の土台（/api/humos・/api/net・/api/entity・/api/rbp/tasks・/api/operations/*）が厚い。

---

## 1. 位置づけ — 「ドメインの画面」ではなく「OS そのものの画面」

| 画面 | 答える問い | レベル |
|---|---|---|
| index.html（日次/防除暦/マスター群） | 「このドメインは回っているか？」 | ドメイン運用 |
| operations.html（運航管理センター） | 「このドメインの業務は期待どおりか？」 | ドメイン運用（PLAN×実状態） |
| stn.html / designer | 「このネットの構造・ログは？」 | 単一ネット |
| **humos.html（新・OS コンソール）** | 「**OS 自体**は健全か？どのドメインでも同じか？」 | **OS（メタ）** |

ドメイン固有の「圃場マスター」等の整理（ドメイン/エンティティ/オブジェクト概念での
再整理）とは**別物**。本画面は「マスター」群を**上から俯瞰**する OS レベルの画面。

### 1.1 ドメイン非依存の根拠（OSである所以の実体）

真界.md「OSである所以」: **汎用化（エンジン不変・ナレッジは可変データ）→ 意思決定の機械化**。

- **不変（エンジン）**: `humos_os` パッケージ（humos=SSS / stn=STN / plan / entity / schema）。
  ドメインに依存しない。
- **可変（ナレッジ）**: 各ドメインが差し込むデータ（entity 行 / RBP タスク / schedule def）。

この画面は、その「不変 vs 可変」を**視覚的に分離**して映すことで、OSである所以を
画面が体現する。

---

## 2. 設計原則

1. **骨格は本質4層そのもの** — 真界.md の ①本質 ②核心 ③機構 ④OSである所以 を
   そのまま4セクションにする。自明に自己言及する。
2. **不変（エンジン）と可変（ナレッジ）を視覚分離** — 各パネルで「OSエンジン（不変）」と
   「差し込まれたドメイン知識（可変）」を色分け（例: 不変=青系、可変=緑系）。
3. **整合性の根拠（replay_ok）を常時表示** — マーキング ≡ M0 リプレイ が OS の健全性の
   核心。OS コンソールに最もふさわしい指標（TS設計 §7.5 整合検証）。
4. **既存コンポーネントを流用** — operations.html のパネル CSS・描画パターンを踏襲し、
   新規 UI フレームワークは導入しない（回帰安全・ドメイン非依存維持）。

---

## 3. パネル構成（4層 × 実データ）

### ① 本質 — 相互開き（閉じた系）

> 真界.md「本質」: 記号界と物理界が**相互に開かれた状態系**。

記号界（HUMOS）↔ 物理界（SOS/接地）の閉ループをライブ表示。
「閉ループが今も生きている」証拠を3点で映す:

- **作動（記号界→物理界）**: 直近の SOS 作動ログ（`/api/humos` の `sos_log` 末尾）
- **認知（物理界→記号界）**: 直近の外部トークン / 接地イベント（`marking` の external 系）
- **整合**: `replay_ok`（マーキング ≡ M0 リプレイ）＋ `actual_state`（`/api/operations/status`）

表示: 閉ループの矢印図（記号界 ⇄ 物理界）＋ 各方向の直近イベント1件 ＋ replay_ok バッジ。

### ② 核心 — トラスト形成（4元素）

> 真界.md「深層の核心」: 判断の**汎用化**＝`認知 → 評価 → 決定 → 投射` の4元素の原子化。
> 確率（記号界）→ 決定（物理界の実情）の過程を**トラスト形成**と呼ぶ。

4元素を横並びで、各元素のライブ状態:

- 各元素: 直近発火（`/api/humos` の `firing_log` を元素別に）＋ 現在のトークン（`marking`）
- **接地タスク**: `/api/rbp/tasks` の5段接地（認知/評価/決定/投射/作動）をタスク別に
  接地率（grounded_count/total）で表示。
- ここが「判断エンジン」の心臓部。**RBP 行列は境界言語で自動導出**（人間は行列を設計しない）
  を映す（緒言登録→自動導出の普遍化）。

### ③ 機構 — SSS / STN / RBP / SOS / 接地

> 真界.md「機構」: 本質と核心を実現する具体機構（SSS/STN/RBP/SOS/接地）。

5機構を縦パネル（or タブ）で:

| 機構 | 映すもの | API |
|---|---|---|
| **SSS** 空間状態システム | マーキングライブ（place→tokens）＋ 発火ログ ＋ **書き込みログ** | `/api/humos`（marking / firing_log / **writing_log**） |
| **STN** 状態遷移ネット | P+T 構造・発火可能トランジション（**不変構造**として） | `/api/net`（places / transitions / firing_enabled） |
| **RBP** 境界言語 | タスク接地（5段）・行列ステータス | `/api/rbp/tasks`（grounding） |
| **SOS** 作動の運動器 | 作動ログ（記号界で実働できるシステムの操作） | `/api/humos`（sos_log） |
| **接地** | `domain → entity → object → component` ツリー ＋ 接地フラグ | `/api/entity` |

- **接地ツリー**が物理界を**ドメイン非依存で**映す（subject_id / domain_id / entity_id /
  object_id の 4-level ID をそのままツリー化）。圃場型/決定導出型の区別は映さない
  （型を問わず同じ操作）。
- STN は stn.html と同構造だが、ここでは「**不変構造**（エンジン）」として色分け。

### ④ OSである所以 — 汎用化

> 真界.md「OSである所以」: 汎用化（エンジン不変・ナレッジは可変データ）→ 意思決定の機械化。

- **エンジン（不変）**: OS バージョン（`humos_os.__version__` = 1.4.0）・モジュール構成
  （humos / stn / plan / entity / schema）・5テーブル（place/token/marking/firing/writing）
- **ナレッジ（可変）**: このドメインに差し込まれた量
  - entity 数（`/api/entity`）
  - RBP タスク数・接地済み数（`/api/rbp/tasks`）
  - schedule def 数（`/api/operations/defs`）
- **ドメイン識別**: `domain_id` / `net_id`（このサーバーがどのドメインか）

---

## 4. API（既存を流用＋最小拡張）

### 4.1 既存 API（そのまま利用）

| パネル | API | 返却キー（実測） |
|---|---|---|
| ①本質 | `GET /api/humos` | `marking` / `firing_log` / `sos_log` / `replay_ok` |
| ①本質 | `GET /api/operations/status` | `now` / `net_id` / `actual_state` / `instances` |
| ②核心 | `GET /api/rbp/tasks` | `tasks[]` = {task_id, name, grounding{grounded, grounded_count, total, stages[]}} |
| ③STN | `GET /api/net` | `places` / `transitions` / `firing_enabled` / `edges` / … |
| ③接地 | `GET /api/entity` | `domain_id` / `entities[]` = {entity_id, objects[] = {object_id, name, attrs, components[]}} |
| ④OS | `GET /api/operations/defs` | schedule def 一覧 |

### 4.2 拡張（新規）

**A. `GET /api/humos` に `writing_log` を追加**

- 現状: `marking` / `firing_log` / `sos_log` / `replay_ok` のみ。
- 追加: `writing_log` = `humos.get_writing_log()`（SSS の認知対象＝空間状態の生データ）。
- 影響: 既存の designer「HUMOS ログ」パネルは新キーを無視して描画 → **後方互換**。

**B. `GET /api/humos/meta` 新設**

```json
{
  "os": {
    "name": "HUMOS",
    "version": "1.4.0",
    "modules": ["humos", "stn", "plan", "entity", "schema"]
  },
  "domain": {
    "subject_id": "mikakura",
    "domain_id": "iichigo",
    "net_id": "mikakura"
  },
  "counts": {
    "entities": 66,          // field 2 + pesticide 64
    "objects": 66,
    "components": 3,
    "rbp_tasks": 8,
    "rbp_tasks_grounded": 1,
    "schedule_defs": 1,
    "places": 10,
    "transitions": 8
  },
  "replay_ok": true
}
```

- 用途: ④OSである所以 パネルの「不変（エンジン）vs 可変（ナレッジ）」表示。
- 実装: `humos_os.__version__` ＋ 各テーブル COUNT ＋ `/api/entity`・`/api/rbp/tasks`
  の集計を再利用。ドメイン固有ロジックは持たない（domain_id/net_id はサーバーが持つ値を返すだけ）。

---

## 5. ナビゲーション

- **ヘッダーメニュー**（☰）に追加: `🧠 HUMOS コンソール`（運航管理センターの隣）。
  - ドメイン固有のタブメニュー（🌱圃場マスター等）ではなく、**OS レベルなのでヘッダー側**に置く。
- **ルート**: `GET /humos` → `humos.html`（server.py に配信分岐追加）。
  - 既存パターン（`/operations` → operations.html）と同一。

---

## 6. 実装段階（増分・回帰安全）

1. **API 拡張**（server.py）
   - `GET /api/humos` に `writing_log` を追加（`humos.get_writing_log()`）。
   - `GET /api/humos/meta` 新設（§4.2-B）。
   - 既存 `/api/humos` の返却キーは変更しない（追加のみ）→ designer 後方互換。
2. **`humos.html` 作成**
   - 4層セクション（①本質 ②核心 ③機構 ④OSである所以）。
   - operations.html のパネル CSS・描画パターンを流用。
   - 各パネルは §3 の API を取得して描画。不変/可変を色分け。
3. **配信・メニュー**（server.py ＋ index.html）
   - server.py: `GET /humos` → `humos.html` 配信分岐。
   - index.html: ヘッダーメニューに `🧠 HUMOS コンソール` 1行追加。
4. **検証**
   - API: `/api/humos/meta`・`/api/humos`（writing_log 追加）の返却確認。
   - Playwright: 4層セクション描画・replay_ok バッジ・接地ツリー（domain→entity→object→component）・4元素接地率。
   - 既存回帰: entity 7/7・designer「HUMOS ログ」パネル（writing_log 追加で壊れていない）等。
5. **（後続・別ドメイン証明）DC サーバーにも同一 `humos.html` を配信**
   - STB と同一 UI を DC が配信し、`domain_id="dc"` / `net_id=genki_key` で映ることを確認。
   - ＝「OSである所以（汎用化）」の**実証**。

---

## 7. スコープ外（今回はやらない）

- 圃場マスター等の**ドメイン固有マスター群**のドメイン/エンティティ/オブジェクト概念での
  再整理（別タスク。本画面はそれを上から俯瞰する OS レベル）。
- 書き込み系（entity 編集・RBP 編集）— 本画面は**閲覧・監視**中心。編集は既存画面で。
- 状態予測（カルマンフィルタ、SSS の予定機能）— 未実装の機構。
- DC への配信（§6-5）— 後続フェーズ。

---

## 8. 参照

- 本質4層・機構・OSである所以: [真界.md](真界.md)「本質（4層）」
- 統一版設計: [真界哲学_HUMOS設計.md](真界哲学_HUMOS設計.md)
- 境界言語（RBP）/ 緒言登録→自動導出: [TS設計.md](TS設計.md) §12.7
- OS コア: `.venv/lib/python3.10/site-packages/humos_os/`（humos / stn / plan / entity / schema）
- 既存コンソール UI: `operations.html`（パネル CSS・描画パターンの流用元）
