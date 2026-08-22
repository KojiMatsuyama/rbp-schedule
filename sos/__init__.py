#!/usr/bin/env python3
"""SOS — System Operation Software（システムオペレーションソフト）ライブラリ。

設計思想（業務中心無人思想.md §12 / §13）:
  SOS は業務空間の「運航管理センター」。役割・経路・優先順位・競合・例外・
  安全条件を統合的に調整するトップダウン層であり、実働エージェント群
  （AIエージェント / 作業者 / 作業機械 / 作業ソフト / 委託組織）を部品として
  包含する。判断の中核（認知・評価・決定・投射）はナビゲーターが担い、
  実働の柔軟性を必要とする場面にだけこの層が実働を投下する。

本パッケージは「実働チャンネル」の管理入口。現行では Slack 送信
（sos.slack）が実装済みの実働チャンネルとして登録されている。

使い方:
  import sos
  sos.slack.send_message("...")          # Slack 短文
  sos.slack.send_card(title, sections)   # Slack リッチカード
  sos.slack.is_configured()              # Webhook 設定済みか

実働チャンネルを足す際は、この __init__ に登録すれば各呼び出し元は
`sos.<channel>` で一様に使える。
"""

from . import slack  # 実働チャンネル: Slack 送信プログラム

__all__ = ["slack"]
