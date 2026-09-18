#!/usr/bin/env python3
"""SOS — System Operation Software（システムオペレーションソフト）ライブラリ。

設計思想（業務中心無人思想.md §12 / §13）:
  SOS は業務空間の「運航管理センター」。役割・経路・優先順位・競合・例外・
  安全条件を統合的に調整するトップダウン層であり、実働（人 / 機械 / ロボット /
  ソフトウェア(SOS)）を部品として包含する。判断の中核（認知・評価・決定・投射）は
  ナビゲーターが担い、
  実働の柔軟性を必要とする場面にだけこの層が実働を投下する。

本パッケージは「実働チャンネル」の管理入口。現行では Slack 送信
（sos.slack）が実装済みの実働チャンネルとして登録されている。

使い方:
  import sos
  sos.slack.send_message("...")          # Slack 短文（既存・安定API）
  sos.slack.send_card(title, sections)   # Slack リッチカード
  sos.slack.is_configured()              # Webhook 設定済みか
  sos.membrane.deliver("...")            # IF（膜）ファサード: 作動を IF を跨いで実働に届ける
  sos.membrane.deliver({"type":"...","text":"...","target":{...}})

IF 設計（ts/真界_IF設計.md §8）:
  sos.slack    — Slack 送信実働（既存・安定API。DB シード・jobnet actions が参照）
  sos.adapter  — 実働チャンネルのアダプタ抽象（BaseAdapter / SlackAdapter）
  sos.membrane — IF（膜）ファサード。作動（起動）を IF を跨いで実働に届ける。
                 ①（挙動不変リファクタ）で STN 作動経路（nodes.py）が経由する。

実働チャンネルを足す際は adapter.py にアダプタを追加し、membrane._ADAPTERS に
登録する（②の自動チャネル解決）。
"""

from . import slack       # 実働チャンネル: Slack 送信プログラム（既存・安定API）
from . import membrane    # IF（膜）ファサード: 作動を IF を跨いで実働に届ける

__all__ = ["slack", "membrane"]
