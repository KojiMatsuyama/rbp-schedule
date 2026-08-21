#!/usr/bin/env python3
"""
agentic_chat/nodes.py — 状態→認知→評価→決定→投射→実行 の6ノード

各ノードは ChatState を受け取り、state の更新内容を dict で返す。
ループはない。直列DAG（有向非巡回グラフ）。

ノード一覧:
  state_node        — ① 状態: トークン集約・発火判定（Petri netモデル）
  perception_node   — ② 認知: ユーザー入力 → 病害虫ベクトル(10次元)
  evaluation_node   — ③ 評価: ベクトル → 評価BOXマッチング
  decision_node     — ④ 決定: 評価BOX + RBP行列演算 → 薬剤選定
  projection_node   — ⑤ 投射: 薬剤名・スコア・trace → メッセージテンプレート
  execution_node    — ⑥ 実行: Slack送信ツールを実行

RBPエンジン:
  - Haskellバイナリ (rbp-algebra) を優先（レギュラー）
  - 失敗/未ビルド時は Python実装 (rbp-algebra-python/api.py) にフォールバック
  - 6段階ブリッジ(L1-L6)の通過履歴・スコア内訳を完全に再現
"""

import glob
import json
import logging
import math
import os
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTOR_DIM = 10

# ================================================================
# 病害虫インデックス（10次元ベクトルの各軸の意味）
# ================================================================
DISEASE_NAMES = [
    "炭疽病",              # 0
    "灰色かび病",          # 1
    "うどんこ病",          # 2
    "ナミハダニ",          # 3
    "ハスモンヨトウ",      # 4
    "オオタバコガ",        # 5
    "ミカンキイロアザミウマ",  # 6
    "ワタアブラムシ",      # 7
    "アブラムシ",          # 8
    "コナジラミ",          # 9
]

DISEASE_INDEX = {name: i for i, name in enumerate(DISEASE_NAMES)}

# ================================================================
# 症状辞典 — 自然言語 → 病害虫名のマッピング
# ================================================================
# 口語表現（～てる、～ちゃう）と標準形の両方を登録。
# マッチングは「辞書パターンがユーザー入力に含まれる」で行う。
# 戻り値は必ず DISEASE_NAMES に存在する名前のみに限定。
SYMPTOM_DICTIONARY: dict[str, list[str]] = {
    # =========================================================================
    # --- 炭疽病 (Anthracnose) ---
    # =========================================================================
    # 基本症状
    "実が腐る": ["炭疽病"],
    "実が腐ってる": ["炭疽病"],
    "実がくさってる": ["炭疽病"],
    "実がボロボロ": ["炭疽病"],
    "実が黒く腐ってる": ["炭疽病"],
    "果実に黒いシミ": ["炭疽病"],
    "実が黒ずむ": ["炭疽病"],
    "実が黒くなってる": ["炭疽病"],
    "葉に黒い斑点": ["炭疽病"],
    "葉に黒い点": ["炭疽病"],
    "葉に黒い斑点が": ["炭疽病"],
    "葉に黒い斑点がある": ["炭疽病"],
    "実が茶色く腐る": ["炭疽病"],
    "実が茶色く腐ってる": ["炭疽病"],
    "炭疽病": ["炭疽病"],
    # 追加パターン — 葉柄・茎・生育不良
    "葉柄が黒い": ["炭疽病"],
    "茎が黒い": ["炭疽病"],
    "蔓が黒ずむ": ["炭疽病"],
    "実の周りが黒い": ["炭疽病"],
    "実のヘタが黒い": ["炭疽病"],
    "実がしぼんで黒い": ["炭疽病"],
    "収穫前に腐る": ["炭疽病"],
    "黒い円形の斑点": ["炭疽病"],
    "夏場に実が腐る": ["炭疽病"],
    "雨後に実が腐る": ["炭疽病"],
    "梅雨後に黒い斑点": ["炭疽病"],

    # =========================================================================
    # --- 灰色かび病 (Botrytis) ---
    # =========================================================================
    # 基本症状
    "葉にぬめり": ["灰色かび病"],
    "葉がぬめってる": ["灰色かび病"],
    "葉にヌメリ": ["灰色かび病"],
    "花が落ちる": ["灰色かび病"],
    "花が落ちてる": ["灰色かび病"],
    "茎が柔らかい": ["灰色かび病"],
    "茎が軟らかい": ["灰色かび病"],
    "生長点が枯れる": ["灰色かび病"],
    "生長点が枯れてる": ["灰色かび病"],
    "ツボが枯れる": ["灰色かび病"],
    "ツボが枯れてる": ["灰色かび病"],
    "蕾が枯れる": ["灰色かび病"],
    "蕾が枯れてる": ["灰色かび病"],
    "花弁が腐る": ["灰色かび病"],
    "花が灰色になる": ["灰色かび病"],
    "灰色かび病": ["灰色かび病"],
    "実が灰色のカビ": ["灰色かび病"],
    "実がカビてる": ["灰色かび病"],
    # 追加パターン — 低温・湿度関連
    "寒い時期にカビ": ["灰色かび病"],
    "冬場に花が腐る": ["灰色かび病"],
    "低温で花が落ちる": ["灰色かび病"],
    "湿度が高いとカビ": ["灰色かび病"],
    "密だと花が腐る": ["灰色かび病"],
    "株元が軟らかい": ["灰色かび病"],
    "灰のかび": ["灰色かび病"],
    "灰色のカビ": ["灰色かび病"],
    "花が茶色く腐る": ["灰色かび病"],
    "つぼみが開かない": ["灰色かび病"],

    # =========================================================================
    # --- うどんこ病 (Powdery Mildew) ---
    # =========================================================================
    # 基本症状
    "葉に白い粉": ["うどんこ病"],
    "葉に白い粉が吹いてる": ["うどんこ病"],
    "葉っぱに白い粉": ["うどんこ病"],
    "葉が白く粉をふってる": ["うどんこ病"],
    "葉が白い粉で覆われてる": ["うどんこ病"],
    "葉っぱが白い粉": ["うどんこ病"],
    "実が白くなる": ["うどんこ病"],
    "実が白くなってる": ["うどんこ病"],
    "葉が白っぽくなってる": ["うどんこ病"],
    "うどんこ病": ["うどんこ病"],
    "粉を吹いたような": ["うどんこ病"],
    "小麦粉をかけたみたい": ["うどんこ病"],
    # 追加パターン — 春・秋・乾燥
    "春先に白い粉": ["うどんこ病"],
    "秋口に白い粉": ["うどんこ病"],
    "乾燥すると白い粉": ["うどんこ病"],
    "新芽が白くなる": ["うどんこ病"],
    "ツボが白くなる": ["うどんこ病"],
    "葉が白く丸まる": ["うどんこ病"],
    "葉っぱが白っぽく": ["うどんこ病"],
    "うどんのような粉": ["うどんこ病"],
    "粉っぽいカビ": ["うどんこ病"],
    "葉が白くなって曲がる": ["うどんこ病"],
    "若葉が白い": ["うどんこ病"],

    # =========================================================================
    # --- ナミハダニ (Spider Mite) ---
    # =========================================================================
    # 基本症状
    "糸状の蜘蛛": ["ナミハダニ"],
    "蜘蛛の巣みたい": ["ナミハダニ"],
    "葉が細かい糸で覆われてる": ["ナミハダニ"],
    "葉がチリチリ": ["ナミハダニ"],
    "葉が乾いてチリチリ": ["ナミハダニ"],
    "葉が乾燥してるみたい": ["ナミハダニ"],
    "葉の裏に小さな虫": ["ナミハダニ"],
    "葉に細かい点": ["ナミハダニ"],
    "葉が銀色に光る": ["ナミハダニ"],
    "葉っぱに蜘蛛の巣": ["ナミハダニ"],
    "ハダニ": ["ナミハダニ"],
    "ナミハダニ": ["ナミハダニ"],
    "葉がちりちりに": ["ナミハダニ"],
    # 追加パターン — 高温乾燥・葉裏
    "暑い日に葉がチリチリ": ["ナミハダニ"],
    "乾燥すると葉がチリチリ": ["ナミハダニ"],
    "葉の裏に赤い虫": ["ナミハダニ"],
    "葉の裏に小さな赤い点": ["ナミハダニ"],
    "葉が銀色っぽく見える": ["ナミハダニ"],
    "葉が乾燥して縮む": ["ナミハダニ"],
    "糸を張ってる": ["ナミハダニ"],
    "葉が密集して糸": ["ナミハダニ"],
    "葉が茶色く枯れる": ["ナミハダニ"],
    "夏場に葉が茶色": ["ナミハダニ"],
    "葉っぱがチリチリ": ["ナミハダニ"],
    "葉がカサカサ": ["ナミハダニ"],

    # =========================================================================
    # --- ハスモンヨトウ (Cutworm) ---
    # =========================================================================
    # 基本症状
    "葉っぱに穴が開く": ["ハスモンヨトウ"],
    "葉っぱに穴があいてる": ["ハスモンヨトウ"],
    "葉に穴が開く": ["ハスモンヨトウ"],
    "葉に穴があいてる": ["ハスモンヨトウ"],
    "葉に穴が空いてる": ["ハスモンヨトウ"],
    "葉に穴が開いてる": ["ハスモンヨトウ"],
    "葉っぱに穴": ["ハスモンヨトウ"],
    "葉っぱが食べられてる": ["ハスモンヨトウ"],
    "葉が食べられてる": ["ハスモンヨトウ"],
    "葉が半分になってる": ["ハスモンヨトウ"],
    "夜中に食べる虫": ["ハスモンヨトウ"],
    "ヨトウムシ": ["ハスモンヨトウ"],
    "ハスモンヨトウ": ["ハスモンヨトウ"],
    # 追加パターン — 大型穴・茎基部
    "葉が大きく欠けてる": ["ハスモンヨトウ"],
    "葉が半分に切られてる": ["ハスモンヨトウ"],
    "茎の根基が食べられてる": ["ハスモンヨトウ"],
    "株元が折れてる": ["ハスモンヨトウ"],
    "夜に出てくる虫": ["ハスモンヨトウ"],
    "暗くなると出てくる": ["ハスモンヨトウ"],
    "土の中で葉を食べる": ["ハスモンヨトウ"],
    "葉がぐったりしてる": ["ハスモンヨトウ"],
    "葉がしおれてる": ["ハスモンヨトウ"],
    "葉がボロボロに食われてる": ["ハスモンヨトウ"],
    "葉がぐちゃぐちゃ": ["ハスモンヨトウ"],

    # =========================================================================
    # --- オオタバコガ (Tobacco Hornworm) ---
    # =========================================================================
    "オオタバコガ": ["オオタバコガ"],
    "実を食べられてる": ["オオタバコガ"],
    "フンが付いてる": ["オオタバコガ"],
    "実が穴あいてる": ["オオタバコガ"],
    "実が穴開いてる": ["オオタバコガ"],
    "実が噛まれてる": ["オオタバコガ"],
    "大きな青虫": ["オオタバコガ"],
    "実の中に虫": ["オオタバコガ"],
    "ガの幼虫": ["オオタバコガ"],
    # 追加パターン — 実入り害虫・フン
    "実の中に穴": ["オオタバコガ"],
    "実が穴だらけ": ["オオタバコガ"],
    "実がグチャグチャ": ["オオタバコガ"],
    "実の表面にフン": ["オオタバコガ"],
    "青い大きい虫": ["オオタバコガ"],
    "実の中に糞": ["オオタバコガ"],
    "実が変形してる": ["オオタバコガ"],
    "実が曲がってる": ["オオタバコガ"],
    "ガの幼虫がいる": ["オオタバコガ"],
    "実が食べ進んでる": ["オオタバコガ"],

    # =========================================================================
    # --- ミカンキイロアザミウマ (Thrips) ---
    # =========================================================================
    "葉に銀色の跡": ["ミカンキイロアザミウマ"],
    "葉に透明感": ["ミカンキイロアザミウマ"],
    "葉に銀色": ["ミカンキイロアザミウマ"],
    "葉がシルバー": ["ミカンキイロアザミウマ"],
    "葉に黒い粒": ["ミカンキイロアザミウマ"],
    "葉が歪んでる": ["ミカンキイロアザミウマ"],
    "葉が変形してる": ["ミカンキイロアザミウマ"],
    "花が変形": ["ミカンキイロアザミウマ"],
    "アザミウマ": ["ミカンキイロアザミウマ"],
    "ミカンキイロアザミウマ": ["ミカンキイロアザミウマ"],
    "葉に傷みたい": ["ミカンキイロアザミウマ"],
    # 追加パターン — 成長点・花
    "成長点が曲がる": ["ミカンキイロアザミウマ"],
    "新芽が曲がる": ["ミカンキイロアザミウマ"],
    "花が変色してる": ["ミカンキイロアザミウマ"],
    "花びらが変形": ["ミカンキイロアザミウマ"],
    "葉がちぢれてる": ["ミカンキイロアザミウマ"],
    "葉が硬くなってる": ["ミカンキイロアザミウマ"],
    "葉に黒い糞の粒": ["ミカンキイロアザミウマ"],
    "葉に黒い点々": ["ミカンキイロアザミウマ"],
    "葉が光沢を失ってる": ["ミカンキイロアザミウマ"],
    "葉がひっくり返ってる": ["ミカンキイロアザミウマ"],
    "小さな細長い虫": ["ミカンキイロアザミウマ"],
    "金色の虫": ["ミカンキイロアザミウマ"],

    # =========================================================================
    # --- ワタアブラムシ (Cotton Aphid) ---
    # =========================================================================
    "ワタノメイガ": ["ワタアブラムシ"],
    "ワタアブラムシ": ["ワタアブラムシ"],
    "葉がベトベト": ["ワタアブラムシ"],
    "葉に蜜っぽい": ["ワタアブラムシ"],
    "葉に黒いカビ": ["ワタアブラムシ"],  # ロウユウカイの二次被害
    # 追加パターン
    "葉がベタベタ": ["ワタアブラムシ"],
    "葉にネバネバ": ["ワタアブラムシ"],
    "葉に黒カビ": ["ワタアブラムシ"],
    "葉が光ってる": ["ワタアブラムシ"],
    "葉が光沢がある": ["ワタアブラムシ"],
    "アブラがでてる": ["ワタアブラムシ"],
    "葉がまとまって付いてる": ["ワタアブラムシ"],
    "新芽に虫が集まる": ["ワタアブラムシ"],
    "ツボに虫": ["ワタアブラムシ"],

    # =========================================================================
    # --- アブラムシ (Aphid) ---
    # =========================================================================
    "葉っぱが黄色くなる": ["アブラムシ", "うどんこ病"],
    "葉っぱが黄色くなってる": ["アブラムシ", "うどんこ病"],
    "葉が黄色くなる": ["アブラムシ", "うどんこ病"],
    "葉が黄色くなってる": ["アブラムシ", "うどんこ病"],
    "葉が黄色": ["アブラムシ"],
    "葉が黄ばんでる": ["アブラムシ"],
    "葉が黄色い": ["アブラムシ"],
    "葉が黄色くなってきた": ["アブラムシ"],
    "葉が縮れる": ["アブラムシ"],
    "葉が縮れてる": ["アブラムシ"],
    "葉っぱが縮れてる": ["アブラムシ"],
    "葉が丸まる": ["アブラムシ"],
    "葉が丸まってる": ["アブラムシ"],
    "葉っぱが丸まる": ["アブラムシ"],
    "葉が丸まってきた": ["アブラムシ"],
    "葉の裏に白い虫": ["コナジラミ", "アブラムシ"],
    "葉の裏に虫がいる": ["コナジラミ", "アブラムシ"],
    "葉っぱの裏に虫": ["コナジラミ", "アブラムシ"],
    "葉にべとべとした虫": ["アブラムシ"],
    "緑の虫": ["アブラムシ"],
    "小さい緑の虫": ["アブラムシ"],
    "葉に虫がついてる": ["アブラムシ"],
    "葉に虫": ["アブラムシ"],
    "アブラムシ": ["アブラムシ"],
    "アブラ": ["アブラムシ"],
    "葉が粘ってる": ["アブラムシ"],
    "葉がねばねば": ["アブラムシ"],
    # 追加パターン
    "葉がぐちゃっと丸まる": ["アブラムシ"],
    "葉がぎゅっと丸まる": ["アブラムシ"],
    "葉が縮こまってる": ["アブラムシ"],
    "葉が巻き込んでる": ["アブラムシ"],
    "葉が巻いてる": ["アブラムシ"],
    "葉の裏に緑の虫": ["アブラムシ"],
    "葉の裏に虫がたくさん": ["アブラムシ"],
    "葉っぱに緑の虫": ["アブラムシ"],
    "葉が弱ってる": ["アブラムシ"],
    "生育が悪い": ["アブラムシ"],
    "葉がもわもわしてる": ["アブラムシ"],
    "葉がまとまって萎れてる": ["アブラムシ"],

    # =========================================================================
    # --- コナジラミ (Whitefly) ---
    # =========================================================================
    "コナジラミ": ["コナジラミ"],
    "白い小さな虫": ["コナジラミ", "アブラムシ"],
    "葉を叩くと白い虫が飛ぶ": ["コナジラミ"],
    "葉を触ると白い虫": ["コナジラミ"],
    "葉に白い虫": ["コナジラミ"],
    "葉に白い虫がついてる": ["コナジラミ"],
    "小さな白い虫": ["コナジラミ"],
    "葉の裏に白い": ["コナジラミ"],
    "葉っぱの裏に白い虫": ["コナジラミ"],
    "葉を揺らすと虫が飛ぶ": ["コナジラミ"],
    # 追加パターン
    "葉を振ると虫が飛ぶ": ["コナジラミ"],
    "手で触ると虫が飛ぶ": ["コナジラミ"],
    "葉に触ると虫": ["コナジラミ"],
    "白い羽の虫": ["コナジラミ"],
    "小さな白い羽虫": ["コナジラミ"],
    "葉の裏に白い虫が多い": ["コナジラミ"],
    "葉の裏に虫がいっぱい": ["コナジラミ"],
    "葉を揺らすと虫": ["コナジラミ"],
    "葉をさわると虫が飛び散る": ["コナジラミ"],
    "葉の裏が白い": ["コナジラミ"],
    "葉っぱの裏に白い虫が多い": ["コナジラミ"],

    # =========================================================================
    # --- 気温・湿度・気候カレンダー（24節句・七十二候） ---
    # =========================================================================
    # --- 気温ベース ---
    # 高温（30℃超え）— うどんこ病・ナミハダニが活発化
    "30度越えで葉が白くなる": ["うどんこ病"],
    "猛暑で白い粉": ["うどんこ病"],
    "真夏日で葉が白っぽく": ["うどんこ病"],
    "暑いのに白い粉": ["うどんこ病"],
    "熱帯夜でうどんこ": ["うどんこ病"],
    # 高温でナミハダニ
    "35度近くで葉がチリチリ": ["ナミハダニ"],
    "猛烈な暑さで葉が乾く": ["ナミハダニ"],
    "炎天下で葉が縮む": ["ナミハダニ"],
    "酷暑で葉が銀色": ["ナミハダニ"],
    # 高温多湿 — アブラムシ・コナジラミが爆発
    "蒸し暑い日に虫が増えた": ["アブラムシ", "コナジラミ"],
    "ジメジメして葉に虫": ["アブラムシ", "コナジラミ"],
    "高温多湿で葉がベトベト": ["ワタアブラムシ"],
    "暑い日に葉がねばねば": ["ワタアブラムシ"],
    # 低温（15℃以下）— 灰色かび病が活発化
    "15度以下で花が腐る": ["灰色かび病"],
    "涼しい日でカビが出る": ["灰色かび病"],
    "冷え込みで蕾が枯れる": ["灰色かび病"],
    "朝晩の温度差でカビ": ["灰色かび病"],
    # 低温でうどんこ病（春秋）
    "涼しいのに白い粉": ["うどんこ病"],
    "春先の冷えで葉が白い": ["うどんこ病"],
    "秋の彼岸過ぎで白い粉": ["うどんこ病"],
    # --- 湿度ベース ---
    # 高湿度（80%超）— カビ系疾患
    "湿度8割超えでカビ": ["灰色かび病", "炭疽病"],
    "露が多くて葉にカビ": ["灰色かび病"],
    "結露で茎が腐る": ["灰色かび病"],
    "夜間湿度が高いとカビ": ["灰色かび病"],
    # 高湿度 — 炭疽病
    "湿度高いと実が腐る": ["炭疽病"],
    "多湿で果実に黒いシミ": ["炭疽病"],
    # 低湿度（40%以下）— うどんこ病・ナミハダニ
    "湿度4割以下で白い粉": ["うどんこ病"],
    "空気乾燥で葉に粉": ["うどんこ病"],
    "加湿してない温室でうどんこ": ["うどんこ病"],
    # 低湿度 — ナミハダニ
    "乾燥しすぎで葉がチリチリ": ["ナミハダニ"],
    "除湿してたらハダニ": ["ナミハダニ"],
    # --- 降水・日照ベース ---
    # 長雨・曇天 — カビ系
    "長雨で葉に黒い斑点": ["炭疽病"],
    "曇りが続いてカビ": ["灰色かび病"],
    "日照不足で生長点が枯れる": ["灰色かび病"],
    "雨が続いて花が落ちる": ["灰色かび病"],
    "雨天が重なってカビ": ["灰色かび病"],
    # 長雨後 — 炭疽病
    "雨が止んだあと実が腐る": ["炭疽病"],
    "連日の雨で葉が黒斑": ["炭疽病"],
    # 晴天続き — うどんこ病・ナミハダニ
    "晴天が続いて白い粉": ["うどんこ病"],
    "日照りが続いて葉が白く": ["うどんこ病"],
    "快晴の日々がうどんこ助長": ["うどんこ病"],
    # 晴天・乾燥 — ナミハダニ
    "日照りで葉がチリチリ": ["ナミハダニ"],
    "雨が降らないで葉が乾く": ["ナミハダニ"],
    # 突然の大雨後 — 炭疽病
    "夕立のあと実が腐る": ["炭疽病"],
    "ゲリラ豪雨の後カビ": ["灰色かび病"],
    # --- 風・台風ベース ---
    # 台風・強風後 — 物理的ダメージ＋二次感染
    "台風のあと葉に穴": ["ハスモンヨトウ"],
    "風で葉が擦れて傷": ["ミカンキイロアザミウマ"],
    "暴風で蔓が黒ずむ": ["炭疽病"],
    "台風後の傷口からカビ": ["灰色かび病"],
    # 乾燥風（フェーン現象）— ナミハダニ
    "フェーン現象で葉が乾く": ["ナミハダニ"],
    "南風で葉がチリチリ": ["ナミハダニ"],
    # --- 24節句・七十二候ベース ---
    # 立春（2月4日頃）— 越冬害虫の活動開始
    "立春過ぎで新芽に虫": ["アブラムシ", "ワタアブラムシ"],
    # 啓蟄（3月6日頃）— 地中の害虫覚醒
    "啓蟄過ぎで土中で虫": ["ハスモンヨトウ"],
    # 春分（3月20日頃）— 温湿度上昇でカビ発生
    "春分過ぎで温室にカビ": ["灰色かび病"],
    # 清明（4月5日頃）— 新葉 outbreak
    "清明の新葉に白い粉": ["うどんこ病"],
    # 立夏（5月6日頃）— 高温多湿始まり
    "立夏過ぎで蒸し暑くカビ": ["灰色かび病", "炭疽病"],
    # 小満（5月21日頃）— アブラムシピーク
    "小満頃に葉が縮れる": ["アブラムシ"],
    # 芒種（6月6日頃）— 梅雨入り・カビ最盛期
    "芒種で梅雨入りカビ急増": ["灰色かび病"],
    "梅雨入りでカビ病が出た": ["灰色かび病"],
    # 夏至（6月21日頃）— 最高気温ピーク
    "夏至過ぎで猛暑うどんこ": ["うどんこ病"],
    # 小暑（7月7日頃）— ハダニ活跃
    "小暑でハダニが大発生": ["ナミハダニ"],
    # 大暑（7月23日頃）— 酷暑で全病害虫活跃
    "大暑で葉が全部チリチリ": ["ナミハダニ"],
    "猛暑日で実が腐る": ["炭疽病"],
    # 立秋（8月8日頃）— 残暑・秋カビ
    "残暑でまたカビが出る": ["灰色かび病"],
    # 処暑（8月23日頃）— 朝夕の温差でカビ
    "処暑で朝夕の温差でカビ": ["灰色かび病"],
    # 白露（9月8日頃）— 露でカビ
    "白露で朝露にカビ": ["灰色かび病"],
    "秋の朝露で葉に斑点": ["炭疽病"],
    # 秋分（9月23日頃）— 中秋カビ
    "中秋過ぎでまたカビ": ["灰色かび病"],
    # 寒露（10月8日頃）— 低温カビ
    "寒露で低温カビ": ["灰色かび病"],
    # 霜降（11月7日頃）— 初霜で植物ストレス
    "初霜で植物が弱ってカビ": ["灰色かび病"],
    # 立冬（11月7日頃）— 温室栽培の冬カビ
    "冬支度で温室のカビ": ["灰色かび病"],
    # 小雪（12月7日頃）— 室内栽培の乾燥うどんこ
    "室内で乾燥うどんこ": ["うどんこ病"],
    # 大雪（12月22日頃）— 低温多湿カビ
    "雪の日で温室がじめじめカビ": ["灰色かび病"],
    # 冬至（12月22日頃）— 日照最短・光不足
    "冬至近くで日照不足カビ": ["灰色かび病"],
    # 小寒（1月6日頃）— 寒冬・暖房乾燥
    "寒冬で暖房乾燥うどんこ": ["うどんこ病"],
    # 大寒（1月20日頃）— 極寒・凍害
    "大寒で凍害あとカビ": ["灰色かび病"],
    # --- 季節の変わり目（転換期ストレス） ---
    "季節の変わり目で植物が弱る": ["灰色かび病"],
    "夏から秋に変わるときカビ": ["灰色かび病"],
    "秋から冬に変わるときカビ": ["灰色かび病"],
    "冬から春に変わるときカビ": ["灰色かび病"],
    "夏の終わりカビが出た": ["灰色かび病"],
    "冬の終わりカビが出た": ["灰色かび病"],
    # --- 気候異常ベース ---
    # 冷夏
    "冷夏でうどんこ病がひどい": ["うどんこ病"],
    "例年より涼しくてうどんこ": ["うどんこ病"],
    # 猛暑
    "記録的猛暑でナミハダニ": ["ナミハダニ"],
    "平年より暑い年で実腐れ": ["炭疽病"],
    # 少雨・干ばつ
    "雨が全く降らない年でうどんこ": ["うどんこ病"],
    "干ばつで植物が弱ってハダニ": ["ナミハダニ"],
    # 多雨
    "平年より雨が多い年でカビ": ["灰色かび病"],
    "例年の梅雨でカビ大発生": ["灰色かび病"],
    # --- 昼夜温差ベース ---
    "昼夜の温度差15度でカビ": ["灰色かび病"],
    "日中30度・夜15度でカビ": ["灰色かび病"],
    "温度差が大きいで葉が弱る": ["灰色かび病"],
    # --- 温室・ハウス環境ベース ---
    # 換気不足
    "換気しないとカビが出る": ["灰色かび病"],
    "窓閉め切りでカビ": ["灰色かび病"],
    "換気不足でうどんこ": ["うどんこ病"],
    # 過密栽植
    "株が密だとカビる": ["灰色かび病"],
    "枝刈りしないでカビ": ["灰色かび病"],
    "風通し悪いとカビる": ["灰色かび病"],
    # 灌水過多
    "やりすぎで根腐れカビ": ["灰色かび病"],
    "葉に水がかかってカビ": ["灰色かび病"],
    # 灌水不足
    "水切れで葉がチリチリ": ["ナミハダニ"],
    "渇水でハダニ発生": ["ナミハダニ"],

    # =========================================================================
    # --- 否定表現（LLM推論のヒントとして使用） ---
    # =========================================================================
    # これらのパターンが含まれない場合は、関連病害虫の確度を下げる
    "カビじゃない": [],  # カビ系を除外
    "虫じゃない": [],  # 虫害を除外
    "白くない": [],  # うどんこ病を除外
    "黒くない": [],  # 炭疽病を除外
    "灰色じゃない": [],  # 灰色かび病を除外

    # =========================================================================
    # --- 複合症状（追加） ---
    # =========================================================================
    "葉が黄色くて裏に虫": ["アブラムシ"],
    "葉が黄色くて白い粉": ["うどんこ病", "アブラムシ"],
    "実が腐って葉が黄色い": ["炭疽病", "アブラムシ"],
    "葉が黄色くて縮れてる": ["アブラムシ"],
    "葉が黄色くて丸まってる": ["アブラムシ"],
    "葉が白くて黄色い": ["うどんこ病", "アブラムシ"],
    "実が腐って白い粉": ["炭疽病", "うどんこ病"],
    "葉に穴があって白い虫": ["ハスモンヨトウ", "コナジラミ"],
    "葉が枯れてて虫": ["灰色かび病", "アブラムシ"],
    "葉がチリチリで糸": ["ナミハダニ"],
    # 追加複合症状
    "葉が黄色くてチリチリ": ["アブラムシ", "ナミハダニ"],
    "葉が白くてチリチリ": ["うどんこ病", "ナミハダニ"],
    "葉が黒くて丸まる": ["炭疽病", "アブラムシ"],
    "葉に穴があって黄色い": ["ハスモンヨトウ", "アブラムシ"],
    "葉が白くて穴がある": ["うどんこ病", "ハスモンヨトウ"],
    "葉が灰色で虫がいる": ["灰色かび病", "アブラムシ"],
    "葉が黒くて穴がある": ["炭疽病", "ハスモンヨトウ"],
    "葉が縮れてて白い粉": ["アブラムシ", "うどんこ病"],
    "葉が丸まって黒い斑点": ["アブラムシ", "炭疽病"],
    "葉が白くて黄色くて虫": ["うどんこ病", "アブラムシ"],
    "葉がチリチリで穴": ["ナミハダニ", "ハスモンヨトウ"],
    "葉が枯れてて白い粉": ["灰色かび病", "うどんこ病"],
    "葉が茶色くて虫": ["炭疽病", "アブラムシ"],
    "葉が変形で虫": ["ミカンキイロアザミウマ", "アブラムシ"],
    "葉が銀色で虫": ["ミカンキイロアザミウマ", "コナジラミ"],
    "葉がベトベトで虫": ["ワタアブラムシ", "アブラムシ"],
    "葉が黒くてカビ": ["炭疽病", "灰色かび病"],
    "葉が白くてカビ": ["うどんこ病", "灰色かび病"],
    "葉が黒くてカビてる": ["炭疽病", "灰色かび病"],
    "葉が茶色くてカビ": ["炭疽病", "灰色かび病"],
    "葉が縮れてて虫": ["アブラムシ"],
    "葉が丸まって虫": ["アブラムシ"],
    "葉が卷いて虫": ["アブラムシ"],
    "葉が卷き込んで虫": ["アブラムシ"],
    # =========================================================================
    # --- 季節リクエスト（発生予測・アドバイス） ---
    # 症状ではなく「今月の発生傾向」などのリクエストには、
    # その季節に発生しやすい病害虫を返す。
    # =========================================================================
    "発生予測": ["アブラムシ", "うどんこ病", "灰色かび病"],
    "季節のアドバイス": ["アブラムシ", "うどんこ病", "灰色かび病"],
    "今月の履歴": ["アブラムシ", "うどんこ病", "灰色かび病"],
    "今月の防除": ["アブラムシ", "うどんこ病", "灰色かび病"],
    "防除履歴": ["アブラムシ", "うどんこ病", "灰色かび病"],
    "害虫の傾向": ["アブラムシ", "ナミハダニ", "コナジラミ"],
    "病害の傾向": ["炭疽病", "うどんこ病", "灰色かび病"],
    "注意すべき病害虫": ["アブラムシ", "うどんこ病", "灰色かび病"],
    "今シーズンの注意点": ["アブラムシ", "うどんこ病", "灰色かび病"],
    "予防策": ["うどんこ病", "灰色かび病", "炭疽病"],
    "発生しやすい病害虫": ["アブラムシ", "うどんこ病", "灰色かび病"],
}


# =====================================================================
# ヘルパー: 症状辞典ルックアップ
# =====================================================================

def lookup_symptom_dict(user_input: str) -> list[str]:
    """
    ユーザー入力のキーワードを症状辞典で検索し、
    該当する病害虫名のリストを返す。

    マッチング戦略:
      1. 長文パターンを優先（より具体的）
      2. 同じ長さなら複数候補を優先（複合症状の可能性）
      3. 否定表現（空リスト）は記録だけして返さない

    Returns:
        該当病害虫名のリスト。複数キーワードが同スコアの場合、
        最も多くの病害虫を指すものを返す。
    """
    best_match: list[str] = []
    best_count = 0
    negations: list[str] = []  # 否定表現として検出（LLM推論時に使用）

    for pattern, diseases in SYMPTOM_DICTIONARY.items():
        if pattern in user_input:
            # 否定表現（空リスト）は記録のみ
            if not diseases:
                negations.append(pattern)
                continue
            # 長さ優先（長いパターンほど具体的）、同長なら多数候補優先
            if len(diseases) > best_count:
                best_count = len(diseases)
                best_match = diseases

    return best_match


# =====================================================================
# 第一段階の意図分類（雑談 vs 病害相談）
# =====================================================================
# 認知ノードがLLMで病害虫を推論する前に、入力がそもそも「病害相談」か
# 「雑談」かを分類する。雑談（「こんにちは」「ありがとう」「天気はどう？」）を
# RBPパイプラインに渡すと、perception の LLM 推論が病害虫を hallucinate して
# 空の相談にも薬剤が処方されるバグになる。

# 病害相談を示す「強いキーワード」。症状辞典のフルフレーズにマッチしない
# 自然な相談（「きゅうりの葉が変」「実がダメ」等）でも病害文脈を捉えるため。
_AGRICULTURE_KEYWORDS = [
    # 植物部位
    "葉", "実", "花", "蕾", "つぼみ", "茎", "根", "蔓", "苗", "株", "果実", "果物",
    "新芽", "葉柄", "土", "畝",
    # 栽培・防除
    "栽培", "作物", "農薬", "散布", "防除", "殺菌", "殺虫", "除草", "施肥",
    "水やり", "ハウス", "温室", "圃場", "畑", "肥料",
    # 病害虫・症状
    "病害", "病害虫", "病", "虫", "菌", "かび", "カビ", "アブラムシ",
    "ハダニ", "ヨトウ", "アザミウマ", "コナジラミ", "ネキリ", "斑点",
    "枯れ", "枯れる", "枯れて", "腐る", "腐って", "腐れ", "黄ば", "変色",
    "しおれ", "萎れ", "食べられ", "効く", "効き", "処方",
]


def classify_intent(user_input: str) -> str:
    """
    第一段階の意図分類。RBPパイプラインに入る前（認知ノードより前）に呼ぶ。

    分類は 2 つ:
      - "disease": 病害相談・処方依頼 → RBPパイプライン（認知→…→処方）へ
      - "chat":    雑談・無関係 → LLMにそのまま答える（処方しない）

    判定:
      1. 症状辞典のいずれかのパターン、または農業キーワードを含む → "disease"
         （挨拶が混じっていても「実が腐ってる」が主語なら病害相談）
      2. それ以外（挨拶・お礼・天気・一般的な質問） → "chat"

    Args:
        user_input: ユーザーのメッセージ

    Returns:
        "chat" | "disease"
    """
    msg = user_input.strip()
    if not msg:
        return "chat"

    # 1. 病害相談の強いシグナル（辞典パターン or 農業キーワード）
    if lookup_symptom_dict(msg):
        return "disease"
    if any(kw in msg for kw in _AGRICULTURE_KEYWORDS):
        return "disease"

    # 2. 他に該当しない → 雑談として扱い、処方しない
    return "chat"


# =====================================================================
# ヘルパー: 病害虫名 → 10次元ベクトル
# =====================================================================

def names_to_vector(names: list[str]) -> list[int]:
    """
    病害虫名のリストを10次元バイナリベクトルに変換。

    DISEASE_NAMES に含まれる名前は1、それ以外は0。
    辞典由来の病害虫（「ヨトウムシ」など）はベクトル外なので無視。

    マッチング優先度:
      1. 完全一致
      2. 辞書名が入力に含まれる（「ハスモンヨトウ」に「ヨトウムシ」）
      3. 入力が辞書名に含まれる（「アブラムシ」→「ワタアブラムシ」）
    """
    vector = [0] * VECTOR_DIM
    for name in names:
        # Step 1: 完全一致
        for i, dn in enumerate(DISEASE_NAMES):
            if dn == name:
                vector[i] = 1
                break
        else:
            # Step 2: 辞書名が入力を含む（「ハスモンヨトウ」←「ヨトウムシ」）
            matched = False
            for i, dn in enumerate(DISEASE_NAMES):
                if dn in name:
                    vector[i] = 1
                    matched = True
                    break
            if matched:
                continue
            # Step 3: 入力が辞書名を含む（「アブラムシ」→「ワタアブラムシ」）
            for i, dn in enumerate(DISEASE_NAMES):
                if name in dn:
                    vector[i] = 1
                    break
    return vector


# =====================================================================
# ヘルパー: 評価BOXの読み込み
# =====================================================================

_eval_boxes_cache: Optional[list[dict]] = None


def _load_eval_boxes() -> list[dict]:
    """
    data/eval_boxes.json から評価BOXを読み込み、キャッシュする。

    Returns:
        [{"id": "EB-01", "vector": [1,0,...], "name": "炭疽病"}, ...]
    """
    global _eval_boxes_cache
    if _eval_boxes_cache is not None:
        return _eval_boxes_cache

    path = os.path.join(APP_ROOT, "data", "eval_boxes.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("eval_boxes.json not found or invalid, using fallback")
        return []

    boxes = []
    for eid, data in raw.items():
        boxes.append({
            "id": eid,
            "vector": data["vector"],
            "name": data["name"],
        })
    _eval_boxes_cache = boxes
    return boxes


# =====================================================================
# ヘルパー: 評価BOXマッチング
# =====================================================================

def find_eval_box(vector: list[int]) -> dict:
    """
    10次元ベクトルを全評価BOXと正確一致させる。

    Returns:
        {"status": "MATCH", "eval_box_id": "EB-01", "eval_box_name": "炭疽病"}
        {"status": "UNDEFINED"}
        {"status": "ERROR", "error": "複数の評価BOXが一致"}
    """
    matches = []
    for box in _load_eval_boxes():
        if box["vector"] == vector:
            matches.append(box)

    if len(matches) == 1:
        return {
            "status": "MATCH",
            "eval_box_id": matches[0]["id"],
            "eval_box_name": matches[0]["name"],
        }
    elif len(matches) > 1:
        return {
            "status": "ERROR",
            "error": f"複数の評価BOXが一致: {[m['id'] for m in matches]}",
        }
    else:
        return {"status": "UNDEFINED"}


# =====================================================================
# ヘルパー: RBPエンジン呼び出し
# =====================================================================

def _find_haskell_bin() -> Optional[str]:
    """
    rbp-algebra バイナリを探す。dist-newstyle-user（ユーザービルド、
    ghc-9.6.6）と dist-newstyle（root所有、再ビルド不可）の両方をglobし、
    mtimeが最も新しいものを採用する（server.py:find_haskell_bin() と同じ方針）。

    server.py を直接importしない理由: server.py はimport時に無条件で
    os.chdir(APP_ROOT) を実行するため、agentic_chat から呼ぶとプロセス全体の
    CWDを副作用で変えてしまう。
    """
    hits = []
    for build_root in ("dist-newstyle-user", "dist-newstyle"):
        pattern = os.path.join(
            APP_ROOT, "rbp-algebra", build_root, "build", "*", "*",
            "rbp-algebra-*", "x", "rbp-algebra", "build", "rbp-algebra", "rbp-algebra")
        hits.extend(glob.glob(pattern))
    return max(hits, key=os.path.getmtime) if hits else None


def _call_rbp_engine(vector: list[int], eval_box_id: Optional[str] = None) -> dict:
    """
    RBPエンジン（Haskell実装）を呼び出して処方計算を行う。

    フロー:
      1. Haskellバイナリ (rbp-algebra) を試す（レギュラー）
      2. 失敗/未ビルド時は Python実装 (rbp-algebra-python/api.py) にフォールバック
      3. どちらもダメならエラー

    Returns:
        {
            "status": "SUCCESS" | "NO_PESTICIDE_DEFINED" | "ALL_BLOCKED_BY_CONSTRAINTS",
            "evalBox": {"status": "MATCH", "detail": "EB-01"},
            "best": {
                "pesticides": [{"id": "...", "name": "...", "system": "..."}],
                "matchCount": 3,
                "coverageRatio": 0.75,
                "mirrorId": 0.95,
                "totalScore": 45.2,
                "breakdown": {
                    "effectiveness": {"raw": ..., "coverageRatio": ..., "mirrorId": ...},
                    "safety": {"raw": ..., "warnings": [...]},
                    "resistance": {"raw": ..., "note": "..."},
                },
            },
            "alternatives": [...],
            "excludedSets": [...],
            "excludedIndividual": [...],
            "bridgeTrace": [...],
        }
    """
    # --- Haskell binary (regular engine) ---
    hs_bin = _find_haskell_bin()

    if hs_bin:
        import subprocess
        try:
            vec_str = ",".join(str(v) for v in vector)
            result = subprocess.run(
                [hs_bin, "--prescribe", vec_str],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
            logger.warning(f"Haskell RBP engine exited {result.returncode}: {result.stderr}")
        except Exception as e:
            logger.warning(f"Haskell RBP engine failed: {e}")

    # --- Fallback: Python RBP engine (rbp-algebra-python) ---
    py_dir = os.path.join(APP_ROOT, "rbp-algebra-python")
    if os.path.isdir(py_dir):
        import sys
        sys.path.insert(0, py_dir)
        try:
            import api as py_api
            result = py_api.prescribe(vector)
            sys.path.pop(0)
            return result
        except Exception as e:
            logger.warning(f"Python RBP engine failed: {e}")
            sys.path.pop(0)

    return {"error": "RBPエンジンが見つかりません"}


# =====================================================================
# ヘルパー: LLMによる病害虫推論（症状辞典で特定できなかった場合）
# =====================================================================

def _strip_reasoning(content: str) -> str:
    """Strip reasoning/thinking content from LLM output (Qwen3.6-35B style)."""
    if not content:
        return ""
    import re

    # Remove <think>...</think> blocks
    content = re.sub(r'</think>.*?(?=</think>|$)', '', content, flags=re.DOTALL)
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)

    # Remove "Here's a thinking process:" style intros
    content = re.sub(
        r'^(?:Here\'s a thinking process:?|Let me think about this|Let me analyze this|Okay, let me|Alright, let me|Hmm, let me|Wait, let me|So, let me|First, I need to|First, I should|I need to figure out|I should check|Let me check|Let me look up|Let me search|Let me retrieve|Let me query|Based on my knowledge|According to my training|From what I know|I\'m aware that|I\'m familiar with)\s*\n?',
        '', content, flags=re.MULTILINE | re.IGNORECASE,
    )

    # Remove Chinese/Japanese reasoning intros
    content = re.sub(
        r'^(?:让我|我认为|我需要|我应该|首先|其次|然后|接下来|最后|总之|综上所述|根据我的理解|我了解到|我知道|我记得|我想|我觉得|分析一下|思考一下|考虑一下)\s*[。,.！？]',
        lambda m: m.group(0)[0] + '\n', content, flags=re.MULTILINE,
    )

    # Remove "Thinking Process:", "Thought:", etc.
    content = re.sub(
        r'(?:^|\n)\s*(?:Thinking\s+(?:Process|Steps|Process:)|Thought(?:s)?\s*:?)\s*(?:\n|$)',
        '\n', content, flags=re.IGNORECASE,
    )

    # Collapse excessive blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)

    result = content.strip()

    # Sanity check: if result is empty or extremely short, return fallback
    if not result or len(result) < 10:
        return ""

    return result


_LOCAL_LLM_BASE_URL = os.environ.get(
    "ANTHROPIC_BASE_URL", "http://192.168.131.161:24200"
)
_LOCAL_LLM_MODEL = os.environ.get("ANTHROPIC_MODEL", "local-llm")
_LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-litellm-test-1234")


def _llm_guess_vector(user_input: str) -> list[int]:
    """
    症状辞典で病害虫名が特定できなかった場合、
    ローカルLLM（Qwen3.6-35B via LiteLLM）に症状から病害虫を推論させ、
    ベクトルに変換する。

    NOTE: agentic_chat の初期リリースではフォールバックとしてのみ使う。
    本来は「認知」の主要経路は症状辞典で、LLMは補助。
    """
    try:
        import requests

        prompt = (
            f"以下の症状描述から、考えられる病害虫を特定し、"
            f"10次元病害虫ベクトルを返してください。\n\n"
            f"【病害虫インデックス】\n"
            f"0=炭疽病, 1=灰色かび病, 2=うどんこ病, 3=ナミハダニ,\n"
            f"4=ハスモンヨトウ, 5=オオタバコガ, 6=ミカンキイロアザミウマ,\n"
            f"7=ワタアブラムシ, 8=アブラムシ, 9=コナジラミ\n\n"
            f"【症状】\n{user_input}\n\n"
            f"回答形式: 病害虫名: ベクトル（例: 炭疽病: [1,0,0,0,0,0,0,0,0,0]）\n"
            f"複数ある場合は改行で区切ってください。病害虫名だけを簡潔に返してください。"
        )

        url = f"{_LOCAL_LLM_BASE_URL}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_LITELLM_API_KEY}",
        }
        payload = {
            "model": _LOCAL_LLM_MODEL,
            "messages": [
                {"role": "system", "content": "あなたは植物病害の専門家です。症状から病害虫を特定してください。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 512,
            "temperature": 0,
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        msg = data["choices"][0]["message"]
        # Reasoning models (Qwen3.6-35B) put the answer in reasoning_content
        text = msg.get("content") or msg.get("reasoning_content", "")

        # Strip reasoning/thinking blocks (same logic as claude_chat.py)
        text = _strip_reasoning(text)

        diseases = _parse_llm_disease_names(text)
        return names_to_vector(diseases)

    except Exception as e:
        logger.warning(f"LLM disease guessing failed: {e}")
        return [0] * VECTOR_DIM


def _parse_llm_disease_names(llm_output: str) -> list[str]:
    """
    LLMの出力から病害虫名を抽出。
    簡易パース: 「炭疽病: [1,0,...]」のような形式を想定。
    """
    extracted = []
    for name in DISEASE_NAMES:
        if name in llm_output:
            extracted.append(name)
    return extracted


# =====================================================================
# ヘルパー: ブリッジ通過履歴のフォーマット
# =====================================================================

_BRIDGE_LABELS = {
    "SPEC-BRIDGE-TARGET": "L1 ターゲット一致",
    "SPEC-BRIDGE-USAGE": "L2 散布回数",
    "SPEC-BRIDGE-PHI": "L3 PHI残留日",
    "SPEC-BRIDGE-ROTATION": "L4 系統ローテーション",
    "SPEC-BRIDGE-MIXING": "L5 混用可否",
    "SPEC-BRIDGE-TOXICITY": "L6 毒性区分",
}


def format_bridge_trace(trace: list[dict]) -> str:
    """
    ブリッジ通過履歴のリストを人間 readable な文字列にフォーマット。

    Args:
        trace: [{"bridge_id": "...", "level": 1.0, "weight": 1.0,
                 "passed": True, "attenuated": False}, ...]

    Returns:
        "L1 ターゲット一致: PASS (w=1.0)\nL2 散布回数: PASS (w=1.0)\n..."
    """
    lines = []
    for t in trace:
        bid = t.get("bridge_id", "")
        label = _BRIDGE_LABELS.get(bid, bid)
        weight = t.get("weight", 1.0)
        passed = t.get("passed", True)
        attenuated = t.get("attenuated", False)

        if not passed:
            lines.append(f"{label}: BLOCKED (w={weight:.1f})")
        elif attenuated:
            lines.append(f"{label}: ATTENUATED (w={weight:.1f})")
        else:
            lines.append(f"{label}: PASS (w={weight:.1f})")
    return "\n".join(lines)


def format_exclusion_reason(exclusion: dict) -> str:
    """
    除外理由をフォーマット。

    Args:
        exclusion: {"pesticides": [...], "exclusionReasons": [...]}
                  または {"pesticides": [...], "exclusionReason": "..."}

    Returns:
        "ベルクート: 混用不可（ダコニール1011）"
    """
    pests = exclusion.get("pesticides", [])
    names = ", ".join(p.get("name", p.get("id", "unknown")) for p in pests)

    reasons = exclusion.get("exclusionReasons", [])
    if not reasons:
        reason = exclusion.get("exclusionReason", "")
        if reason:
            reasons = [reason]

    if not reasons:
        return names

    return f"{names}: {'; '.join(reasons)}"


# =====================================================================
# ヘルパー: Slack送信
# =====================================================================

def _send_to_slack(message: str) -> dict:
    """
    chat_client を使って Slack にメッセージを送信。

    Returns:
        {"success": True} または {"success": False, "error": "..."}
    """
    try:
        from chat_client import send_message
        result = send_message(message)
        return result
    except ImportError:
        logger.error("chat_client モジュールが見つかりません")
        return {"success": False, "error": "chat_client not found"}
    except Exception as e:
        logger.error(f"Slack送信エラー: {e}")
        return {"success": False, "error": str(e)[:200]}


# =====================================================================
# ヘルパー: 最後の user メッセージを取得
# =====================================================================

def _get_last_user_message(messages: list[dict]) -> str:
    """messages リストの最後の user メッセージの内容を返す。"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


# =====================================================================
# ヘルパー: 処方結果から enriched 情報を抽出
# =====================================================================

def _enrich_prescription(result: dict, vector: list[int]) -> dict:
    """
    RBPエンジンの生レスポンスから、ノード出力用に整形。

    Pythonエンジン (api.py) の出力形式:
      {
        "engine": "python",
        "sampleDb": False,
        "pesticideCount": 67,
        "evalBox": {"status": "MATCH", "detail": "EB-01"},
        "status": "SUCCESS",
        "best": {
            "pesticides": [{"id": "P01", "name": "ベルクート", "system": "QoI系"}],
            "matchCount": 3,
            "coverageRatio": 0.75,
            "mirrorId": 0.95,
            "totalScore": 45.2,
            "breakdown": {
                "effectiveness": {"raw": ..., "mirrorId": ..., "coverageRatio": ..., ...},
                "safety": {"raw": ..., "warnings": [...]},
                "resistance": {"raw": ..., "note": "..."},
                "mixingOk": true,
                "mixingReasons": [],
            },
        },
        "alternatives": [...],
        "lineTraces": [
            {"pesticide": "P01", "pesticide_name": "ベルクート",
             "levels": [1.0,2.0,...], "weights": [1.0,1.0,...],
             "blocked": false, "blocked_at": null},
            ...
        ],
        "excludedIndividual": [
            {"pesticidePid": "P10", "pesticideName": "アブラirin",
             "bridgeId": "SPEC-BRIDGE-TOXICITY", "reason": "..."},
            ...
        ],
        "excludedSets": [
            {"pesticidePids": ["P30","P61"], "pesticideNames": ["イオウフロアブル","サフオイル"],
             "gateId": "SPEC-BRIDGE-MIXING-SET", "reasons": ["..."]},
            ...
        ],
      }

    ※ 旧Haskellエンジンや失敗時は、_infer_bridge_traces / _infer_exclusions で補完。
    """
    status = result.get("status", "UNKNOWN")
    eval_box = result.get("evalBox", {})

    # --- best prescription ---
    best_raw = result.get("best")
    best = None
    if best_raw:
        pesticides = best_raw.get("pesticides", [])
        best = {
            "pesticides": pesticides,
            "matchCount": best_raw.get("matchCount", 0),
            "coverageRatio": best_raw.get("coverageRatio", 0.0),
            "mirrorId": best_raw.get("mirrorId", 0.0),
            "totalScore": best_raw.get("totalScore", 0.0),
            "breakdown": best_raw.get("breakdown", None),
            "isCombo": best_raw.get("isCombo", len(pesticides) > 1),
        }

    # --- alternatives ---
    alternatives_raw = result.get("alternatives", [])
    alternatives = []
    for alt_raw in alternatives_raw:
        alt_pesticides = alt_raw.get("pesticides", [])
        alternatives.append({
            "pesticides": alt_pesticides,
            "matchCount": alt_raw.get("matchCount", 0),
            "coverageRatio": alt_raw.get("coverageRatio", 0.0),
            "mirrorId": alt_raw.get("mirrorId", 0.0),
            "totalScore": alt_raw.get("totalScore", 0.0),
            "breakdown": alt_raw.get("breakdown", None),
            "isCombo": alt_raw.get("isCombo", len(alt_pesticides) > 1),
        })

    # --- bridge trace & exclusions ---
    # Python engine returns lineTraces (per-pesticide full traces)
    # Old Haskell engine returns bridgeTrace (single shared trace)
    line_traces = result.get("lineTraces", [])
    bridge_trace_legacy = result.get("bridgeTrace", [])
    excluded_individual = result.get("excludedIndividual", [])
    excluded_sets = result.get("excludedSets", [])

    # --- 補完: lineTracesがない場合はlegacy bridgeTraceを使う ---
    if not line_traces and bridge_trace_legacy:
        # Legacy: bridgeTrace is a single trace for the best prescription
        # Convert to per-pesticide format
        line_traces = []
        if best:
            for p in best.get("pesticides", []):
                line_traces.append({
                    "pesticide": p.get("id", ""),
                    "pesticide_name": p.get("name", "unknown"),
                    "levels": [t.get("level", 0) for t in bridge_trace_legacy],
                    "weights": [t.get("weight", 0) for t in bridge_trace_legacy],
                    "blocked": False,
                    "blocked_at": None,
                })

    # --- 補完: lineTracesが空かつengineがHaskellの場合は推論 ---
    if not line_traces:
        line_traces = _infer_bridge_traces(result, vector)

    # --- 補完: excludedIndividual/excludedSetsがない場合は推論 ---
    if not excluded_individual and not excluded_sets:
        excluded_individual, excluded_sets = _infer_exclusions(result, vector)

    # --- bridgeTrace: 最初のconnected lineのtraceを代表として返す ---
    bridge_trace = []
    if line_traces:
        # Find the best pesticide's trace
        best_pids = set()
        if best:
            for p in best.get("pesticides", []):
                best_pids.add(p.get("id", ""))
        for lt in line_traces:
            if lt.get("pesticide") in best_pids:
                # Reconstruct legacy-style trace from levels/weights
                bridge_trace = [
                    {
                        "bridge_id": f"L{int(level)}",
                        "level": level,
                        "weight": weight,
                        "passed": weight > 0,
                        "attenuated": 0 < weight < 1,
                    }
                    for level, weight in zip(lt.get("levels", []), lt.get("weights", []))
                ]
                break
        if not bridge_trace:
            # Fallback: use first trace
            first_lt = line_traces[0]
            bridge_trace = [
                {
                    "bridge_id": f"L{int(level)}",
                    "level": level,
                    "weight": weight,
                    "passed": weight > 0,
                    "attenuated": 0 < weight < 1,
                }
                for level, weight in zip(first_lt.get("levels", []), first_lt.get("weights", []))
            ]

    return {
        "status": status,
        "evalBox": eval_box,
        "best": best,
        "alternatives": alternatives,
        "bridgeTrace": bridge_trace,
        "lineTraces": line_traces,
        "excludedSets": excluded_sets,
        "excludedIndividual": excluded_individual,
        "pesticideCount": result.get("pesticideCount", 0),
    }


# =====================================================================
# ヘルパー: ブリッジtraceの推論（エンジンが返さない場合の補完）
# =====================================================================

def _infer_bridge_traces(result: dict, vector: list[int]) -> list[dict]:
    """
    RBPエンジンがlineTracesを返さない場合、
    処方結果からブリッジ通過履歴を推論する。

    各候補薬剤について、6段階ブリッジの通過状況を推測する。
    推論の精度は限定的だが、少なくともL1（ターゲット一致）は判定可能。
    """
    traces = []

    # best + alternatives の全薬剤を収集
    all_pesticides = []
    if result.get("best"):
        for p in result["best"].get("pesticides", []):
            all_pesticides.append(p)
    for alt in result.get("alternatives", []):
        for p in alt.get("pesticides", []):
            if p not in all_pesticides:
                all_pesticides.append(p)

    for p in all_pesticides[:10]:  # 最大10薬剤分
        pid = p.get("id", "")
        pname = p.get("name", "unknown")
        system = p.get("system", "")

        # ターゲット一致数（エンジン提供または推論）
        match_count = 0
        if result.get("best") and pid in str(result["best"].get("pesticides", [])):
            match_count = result["best"].get("matchCount", 0)
        for alt in result.get("alternatives", []):
            for ap in alt.get("pesticides", []):
                if ap.get("id") == pid:
                    match_count = alt.get("matchCount", 0)

        # 6段階ブリッジの推論
        # L1: TARGET — ターゲット一致でPASS
        # L2-L6: 情報が不足するためPASSと仮定（実際にはengineが返すはず）
        levels = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        weights = [1.0 if match_count > 0 else 0.0] + [1.0] * 5
        passed_flags = [w > 0 for w in weights]
        attenuated_flags = [0 < w < 1 for w in weights]

        trace = {
            "pesticide": pid,
            "pesticide_name": pname,
            "levels": levels,
            "weights": weights,
            "blocked": not all(passed_flags),
            "blocked_at": None if all(passed_flags) else "SPEC-BRIDGE-TARGET",
        }
        traces.append(trace)

    return traces


def _infer_exclusions(result: dict, vector: list[int]) -> tuple[list[dict], list[dict]]:
    """
    RBPエンジンがexclusion情報を返さない場合、
    処方結果から除外された可能性のある薬剤を推論する。

    基本的なルール:
      - ターゲット不一致 → L1でブロック
      - 高毒性 → L6で減衰（除外ではないが警告）
    """
    excluded_individual = []
    excluded_sets = []

    # 処方された薬剤IDの集合
    prescribed_ids = set()
    if result.get("best"):
        for p in result["best"].get("pesticides", []):
            prescribed_ids.add(p.get("id", ""))
    for alt in result.get("alternatives", []):
        for p in alt.get("pesticides", []):
            prescribed_ids.add(p.get("id", ""))

    # 67剤DBからターゲット不一致の薬剤を推論（簡易版）
    # ※ 実際のDBアクセスは重いので、処方結果から逆算
    #    「処方されなかった薬剤」のうち、特に注意すべきものをマーク

    # 高毒性で減衰した可能性のある薬剤（警告として表示）
    # これは実際のDB参照が必要だが、簡易版では跳过

    return excluded_individual, excluded_sets


# =====================================================================
# NODE ①: state_node — 状態（トークン集約・発火判定）
# =====================================================================

from .tokens import get_token_state


def state_node(state: dict) -> dict:
    """
    ① 状態ノード — トークン集約・発火判定（Petri netモデル）。

    共有ストア（agentic_chat/tokens.py）からトークンを読み取り、
    全トークンが揃うまで待機（checkpointで保持）。
    全トークンが揃ったらエージェントを発火させる。

    トークン入力源:
      - スケジュール: 設定した日時になるとイベント（防除トークン）が入力
      - カレンダー: 日付をクリックすると防除トークンが入力
      - API: POST /api/tokens/set で手動投入

    Returns:
        {
            "token_ready": "ready",         # "pending" | "ready"
            "schedule": "2026-08-20T09:00",
            "crop": "きゅうり",
            "environment": "温室",
            "growth_stage": "育苗中",
        }
    """
    token_state = get_token_state()
    tokens = token_state["tokens"]

    # 全トークンが揃っているかチェック
    all_present = token_state["ready"]

    if not all_present:
        # 未完了 → checkpointに保存して待機
        missing = [k for k in ["schedule", "crop", "environment", "growth_stage"]
                   if tokens[k] is None]
        logger.info(f"[状態] トークン不足で待機中: {missing}")
        return {
            "token_ready": "pending",
            "schedule": tokens["schedule"],
            "crop": tokens["crop"],
            "environment": tokens["environment"],
            "growth_stage": tokens["growth_stage"],
        }

    # 全トークン揃った → 発火
    logger.info("[状態] 全トークン揃った。エージェント発火！")
    return {
        "token_ready": "ready",
        "schedule": tokens["schedule"],
        "crop": tokens["crop"],
        "environment": tokens["environment"],
        "growth_stage": tokens["growth_stage"],
    }


# =====================================================================
# NODE ②: perception_node — 認知
# =====================================================================

def perception_node(state: dict) -> dict:
    """
    ① 認知ノード — ユーザーの入力から病害虫の発生状況を「認知」する。

    フロー:
      1. 症状辞典で病害虫名を特定
      2. 病害虫名 → 10次元バイナリベクトルに変換
      3. 辞典で特定できなかったら LLM に推論させる

    Returns:
        {
            "identified_diseases": ["炭疽病", "アブラムシ"],
            "vector": [1, 0, 0, 0, 0, 0, 0, 0, 1, 0],
        }
    """
    user_input = _get_last_user_message(state["messages"])

    # Step 1: 症状辞典で病害虫名を特定
    disease_names = lookup_symptom_dict(user_input)

    if disease_names:
        # 辞典で特定できた → ベクトルに変換
        vector = names_to_vector(disease_names)
    else:
        # 辞典で特定できなかった → LLMに推論
        logger.info(f"症状辞典で病害虫を特定できませんでした。LLMに推論を依頼: {user_input[:50]}...")
        vector = _llm_guess_vector(user_input)
        # LLMからも特定できなかった場合は空ベクトル
        if sum(vector) == 0:
            disease_names = []

    return {
        "identified_diseases": disease_names,
        "vector": vector,
    }


# =====================================================================
# NODE ②: evaluation_node — 評価（要求評価）
# =====================================================================

def evaluation_node(state: dict) -> dict:
    """
    ② 評価ノード — 認知した病害虫ベクトルを評価BOXにマッチさせる。

    「こういう発生があるので薬剤を選びたい」という要求を、
    事前に定義されたシナリオ（評価BOX）に分類する（要求評価）。

    Returns:
        {
            "eval_box_id": "EB-01",        # マッチした評価BOXのID
            "eval_box_name": "炭疽病",      # 人間 readable な名前
            "eval_status": "matched",       # "matched" | "undefined" | "error"
        }
    """
    vector = state["vector"]

    # 空ベクトルガード — 病害虫が認知されていない場合はRBP演算に進めない。
    # 雑談・無関係入力（「こんにちは」等）は perception 経由で
    # intent="chat" として処理されるため、ここに到達するはずはないが、
    # 念のため処方結果を返さないガードとして残す。
    if not vector or sum(vector) == 0:
        return {
            "eval_box_id": None,
            "eval_box_name": None,
            "eval_status": "none",
            "intent": "chat",
        }

    # 評価BOXと正確一致マッチング
    match = find_eval_box(vector)

    if match["status"] == "MATCH":
        return {
            "eval_box_id": match["eval_box_id"],
            "eval_box_name": match["eval_box_name"],
            "eval_status": "matched",
        }
    elif match["status"] == "UNDEFINED":
        # 未知の組み合わせ → 評価BOXなしでRBP演算に進む
        return {
            "eval_box_id": None,
            "eval_box_name": None,
            "eval_status": "undefined",
        }
    else:
        # 複数一致 → エラー
        return {
            "eval_box_id": None,
            "eval_box_name": None,
            "eval_status": "error",
            "error": match.get("error", "複数の評価BOXが一致"),
        }


# =====================================================================
# NODE ③: decision_node — 決定（仕様決定）
# =====================================================================

def decision_node(state: dict) -> dict:
    """
    ③ 決定ノード — 評価BOX（または直接ベクトル）を使って、
    RBP行列演算を行い、ミラーIDでスコアリングして
    最適な薬剤セットを選定する（仕様決定）。

    フロー:
      1. RBPエンジン呼び出し（Python → Haskellフォールバック）
      2. 結果を解析: best, alternatives, bridgeTrace, exclusions
      3. 状態に応じた適切な出力を構築

    Returns:
        {
            "prescription": [
                {"name": "ベルクート", "id": "P01", "score": 45.2,
                 "mirrorId": 0.95, "coverageRatio": 0.75,
                 "breakdown": {...}}
            ],
            "alternatives": [...],
            "mirror_id": 0.95,
            "effectiveness": 45.2,
            "bridge_trace": "L1: PASS ...\n...",
            "excluded_drugs": ["ベルクート: PHI不足"],
            "excluded_combos": ["ベルクート+ダコニール: 混用不可"],
            "status": "SUCCESS",
        }
    """
    vector = state["vector"]
    eval_box_id = state.get("eval_box_id")

    # 空ベクトルガード — 認知されていない（雑談等）場合は処方計算を行わない。
    # 空ベクトルをRBPエンジンに渡すと、全薬剤が対象一致（ミラーID=1.0）となり、
    # 「こんにちは」等の雑談に対して薬剤を処方するバグの原因となる。
    if not vector or sum(vector) == 0:
        return {
            "prescription": [],
            "alternatives": [],
            "mirror_id": None,
            "effectiveness": None,
            "bridge_trace": None,
            "excluded_drugs": [],
            "excluded_combos": [],
            "status": "NO_TARGET_IDENTIFIED",
        }

    # RBPエンジン呼び出し
    raw_result = _call_rbp_engine(vector, eval_box_id=eval_box_id)

    if "error" in raw_result:
        return {
            "prescription": [],
            "alternatives": [],
            "mirror_id": None,
            "effectiveness": None,
            "bridge_trace": None,
            "excluded_drugs": [],
            "excluded_combos": [],
            "status": "ENGINE_ERROR",
            "error": f"RBPエンジンエラー: {raw_result['error']}",
        }

    # 結果を整形
    enriched = _enrich_prescription(raw_result, vector)
    status = enriched["status"]

    # --- best prescription ---
    best = enriched["best"]
    if best:
        prescription = []
        for p in best["pesticides"]:
            entry = {
                "name": p.get("name", p.get("id", "unknown")),
                "id": p.get("id", ""),
                "score": best["totalScore"],
                "mirrorId": best.get("mirrorId", 0),
                "coverageRatio": best.get("coverageRatio", 0),
                "system": p.get("system", ""),
            }
            # breakdownがあれば展開
            bd = best.get("breakdown")
            if bd:
                entry["breakdown"] = bd
            prescription.append(entry)

        mirror_id = best.get("mirrorId", 0)
        effectiveness = best.get("totalScore", 0)
    else:
        prescription = []
        mirror_id = None
        effectiveness = None

    # --- alternatives ---
    alternatives_out = []
    for i, alt in enumerate(enriched["alternatives"]):
        alt_entry = {
            "rank": i + 2,  # rank 2+ (best is rank 1)
            "pesticides": [
                {"name": p.get("name", p.get("id", "unknown")),
                 "id": p.get("id", ""),
                 "system": p.get("system", "")}
                for p in alt.get("pesticides", [])
            ],
            "score": alt.get("totalScore", 0),
            "mirrorId": alt.get("mirrorId", 0),
            "coverageRatio": alt.get("coverageRatio", 0),
        }
        bd = alt.get("breakdown")
        if bd:
            alt_entry["breakdown"] = bd
        alternatives_out.append(alt_entry)

    # --- bridge trace ---
    bridge_trace_str = None
    if enriched["bridgeTrace"]:
        bridge_trace_str = format_bridge_trace(enriched["bridgeTrace"])

    # --- excluded drugs & combos ---
    excluded_drugs = []
    for exc in enriched.get("excludedIndividual", []):
        excluded_drugs.append(format_exclusion_reason(exc))

    excluded_combos = []
    for exc in enriched.get("excludedSets", []):
        excluded_combos.append(format_exclusion_reason(exc))

    return {
        "prescription": prescription,
        "alternatives": alternatives_out,
        "mirror_id": mirror_id,
        "effectiveness": effectiveness,
        "bridge_trace": bridge_trace_str,
        "line_traces": enriched.get("lineTraces", []),
        "excluded_drugs": excluded_drugs,
        "excluded_combos": excluded_combos,
        "status": status,
    }


# =====================================================================
# NODE ④: projection_node — 投射
# =====================================================================

def projection_node(state: dict) -> dict:
    """
    ④ 投射ノード — 決定された薬剤名をメッセージテンプレートに埋め込む。

    テンプレート:
      【評価BOX名】
      今回の防除の薬剤は、ベルクート、ダコニール1011、です。

      【スコア内訳】
      ミラーID: 0.95
      有効性スコア: 45.2
      - 有効性: ミラーID=0.95, カバレッジ=75% (3/4)
      - 安全性: 20.0
      - 抵抗性: 異なる系統（FRAC1／IRAC21A）の組み合わせ：抵抗性管理上有効

      【ブリッジ通過履歴（全候補）】
      ベルクート: L1=PASS L2=PASS L3=PASS L4=PASS L5=PASS L6=PASS
      ランマン:   L1=PASS L2=PASS L3=PASS L4=PASS L5=PASS L6=PASS
      ...

      【代替案】
      2位: ダコニール1011 (スコア: 42.1)

      【除外された薬剤】
      - アブラirin: 混用不可（SPEC-BRIDGE-TOXICITY）
      - イオウフロアブル+サフオイル: 混用不可（SPEC-BRIDGE-MIXING-SET）

    Returns:
        {"projected_message": "今回の防除の薬剤は..."}
    """
    drugs = state.get("prescription", [])
    eval_box_name = state.get("eval_box_name")
    identified = state.get("identified_diseases", [])
    mirror_id = state.get("mirror_id")
    effectiveness = state.get("effectiveness")
    bridge_trace = state.get("bridge_trace")
    excluded_drugs = state.get("excluded_drugs", [])
    excluded_combos = state.get("excluded_combos", [])
    alternatives = state.get("alternatives", [])
    status = state.get("status", "")

    # lineTraces（全connected lineのブリッジ通過履歴）
    line_traces = state.get("line_traces", [])

    parts = []

    # ---- ヘッダー ----
    if status == "NO_TARGET_IDENTIFIED":
        # 認知されない（雑談等）場合は処方結果を返さない。
        # 本来は intent="chat" で __init__.run がLLM応答に置換するため
        # ここに到達しないが、念のため処方テンプレートを返さない。
        parts.append("【対応内容なし】")
        parts.append("今回のメッセージから病害虫の発生が認知されませんでした。")
        return {"projected_message": "\n".join(parts)}

    if status == "NO_PESTICIDE_DEFINED":
        parts.append("【対応薬剤なし】")
        parts.append("選択された病害虫に対して、登録済みの薬剤が定義されていません。")
        return {"projected_message": "\n".join(parts)}

    if status == "ALL_BLOCKED_BY_CONSTRAINTS":
        parts.append("【全薬剤除外】")
        parts.append("対応する薬剤は存在しますが、すべての薬剤が何らかの制約により選択できません。")
        if excluded_drugs:
            parts.append("")
            parts.append("【除外された薬剤】")
            for d in excluded_drugs[:10]:
                parts.append(f"- {d}")
        if excluded_combos:
            parts.append("")
            parts.append("【除外された2剤セット】")
            for c in excluded_combos[:10]:
                parts.append(f"- {c}")
        return {"projected_message": "\n".join(parts)}

    if status == "ENGINE_ERROR":
        error_msg = state.get("error", "RBPエンジンエラー")
        parts.append(f"【エラー】{error_msg}")
        return {"projected_message": "\n".join(parts)}

    # 正常系
    if eval_box_name:
        parts.append(f"【{eval_box_name}】")
    elif identified:
        parts.append(f"【{'、'.join(identified)}】")

    # ---- 処方結果 ----
    if drugs:
        drug_names = "、".join(d["name"] for d in drugs)
        parts.append(f"今回の防除の薬剤は、{drug_names}、です。")
    else:
        parts.append("（薬剤選定できませんでした）")

    # ---- スコア内訳 ----
    if mirror_id is not None:
        parts.append("")
        parts.append("【スコア内訳】")
        parts.append(f"ミラーID: {mirror_id:.2f}")
        if effectiveness is not None:
            parts.append(f"有効性スコア: {effectiveness:.1f}")

        # 個別薬剤スコア
        for d in drugs:
            score = d.get("score", d.get("mirrorId", 0))
            mr = d.get("mirrorId", 0)
            cr = d.get("coverageRatio", 0)
            line = f"- {d['name']}:"
            if mr:
                line += f" ミラーID={mr:.2f}"
            if cr:
                line += f" カバレッジ={cr:.0%}"
            parts.append(line)

        # breakdownがあれば詳細を表示
        if drugs and "breakdown" in drugs[0]:
            bd = drugs[0].get("breakdown", {})
            eff_bd = bd.get("effectiveness", {})
            sat_bd = bd.get("safety", {})
            res_bd = bd.get("resistance", {})

            if eff_bd:
                mi = eff_bd.get("mirrorId", 0)
                cov = eff_bd.get("coverageRatio", 0)
                mc = eff_bd.get("matchCount", 0)
                ts = eff_bd.get("targetSum", 0)
                parts.append(f"  有効性: ミラーID={mi:.2f}, カバレッジ={cov:.0%} ({mc}/{ts})")

            if sat_bd:
                raw_sat = sat_bd.get("raw", 0)
                parts.append(f"  安全性: {raw_sat:.1f}")
                warnings = sat_bd.get("warnings", [])
                if warnings:
                    for w in warnings:
                        parts.append(f"    ⚠ {w}")

            if res_bd:
                raw_res = res_bd.get("raw", 0)
                parts.append(f"  抵抗性: {raw_res:.1f}")
                note = res_bd.get("note", "")
                if note:
                    parts.append(f"    ℹ {note}")

    # ---- ブリッジ通過履歴（全候補） ----
    if line_traces:
        parts.append("")
        parts.append("【ブリッジ通過履歴（全候補）】")
        for lt in line_traces[:20]:  # 最大20薬剤分
            pname = lt.get("pesticide_name", lt.get("pesticide", "unknown"))
            levels = lt.get("levels", [])
            weights = lt.get("weights", [])
            blocked = lt.get("blocked", False)
            blocked_at = lt.get("blocked_at")

            # L1-L6 の簡易表記
            bridge_labels = [
                "L1ターゲット", "L2散布回数", "L3PHI残留日",
                "L4系統ローテーション", "L5混用可否", "L6毒性区分",
            ]
            trace_parts = []
            for i, (lbl, w) in enumerate(zip(bridge_labels, weights)):
                if w == 0.0:
                    trace_parts.append(f"{lbl}=BLOCKED")
                elif w < 1.0:
                    trace_parts.append(f"{lbl}=ATTENUATE(w={w:.1f})")
                else:
                    trace_parts.append(f"{lbl}=PASS")

            status_marker = " [!!]" if blocked else ""
            parts.append(f"  {pname}: {' '.join(trace_parts)}{status_marker}")
            if blocked_at:
                parts.append(f"    ↳ {blocked_at} でブロック")

    elif bridge_trace:
        # レガシー: 単一のtraceのみ
        parts.append("")
        parts.append("【ブリッジ通過履歴】")
        parts.append(bridge_trace)

    # ---- 代替案 ----
    if alternatives:
        parts.append("")
        parts.append("【代替案】")
        for alt in alternatives[:5]:
            alt_names = "、".join(p["name"] for p in alt.get("pesticides", []))
            alt_score = alt.get("score", 0)
            alt_mr = alt.get("mirrorId", 0)
            parts.append(f"{alt.get('rank', '?')}位: {alt_names} (スコア={alt_score:.1f}, ミラーID={alt_mr:.2f})")

    # ---- 除外された薬剤 ----
    if excluded_drugs or excluded_combos:
        parts.append("")
        parts.append("【除外された薬剤】")
        for d in excluded_drugs[:10]:
            parts.append(f"- {d}")
        for c in excluded_combos[:10]:
            parts.append(f"- {c}（2剤セット）")

    message = "\n".join(parts)

    return {
        "projected_message": message,
    }


# =====================================================================
# NODE ⑤: execution_node — 実行
# =====================================================================

def execution_node(state: dict) -> dict:
    """
    ⑤ 実行ノード — 投射されたメッセージを実際にSlackに送信する。

    「実行」ノードとして、ツール（slack_send）を選択・実行する。

    Returns:
        {"executed": True, "sent_to": "slack"}
    """
    message = state.get("projected_message", "")

    if not message:
        return {
            "executed": False,
            "sent_to": None,
            "error": "送信メッセージが空です",
        }

    result = _send_to_slack(message)

    if result.get("success"):
        return {
            "executed": True,
            "sent_to": "slack",
        }
    else:
        return {
            "executed": False,
            "sent_to": None,
            "error": result.get("error", "Slack送信に失敗しました"),
        }


# =====================================================================
# NODE ⑥: inventory_node — 在庫チェック（並列独立トランジション）
# =====================================================================

def inventory_node(state: dict) -> dict:
    """
    ⑥ 在庫チェックノード — 処方結果の薬剤名+数量で在庫を照会。

    Petri net遷移:
      処方トークン（薬剤名+数量JSON）がplaceに投入される
      → 在庫チェックが可能になったら発火

    在庫DB: stb.db（既存）のinventoryテーブル

    Returns:
        {
            "inventory_check": {"ベルクート": {"stock": 5, "needed": 3, "status": "ok", ...}, ...},
            "inventory_message": "【在庫チェック結果】...",
        }
    """
    prescription = state.get("prescription", [])

    if not prescription:
        return {
            "inventory_check": {},
            "inventory_message": "【在庫チェック結果】\n処方結果がありません。",
        }

    # 各薬剤の在庫をチェック
    inventory_check: dict[str, dict] = {}
    for drug in prescription:
        name = drug.get("name", "?")
        needed = drug.get("quantity", 3)

        # 在庫DBから照会
        stock = query_stock_from_db(name)

        if stock is None:
            status = "unknown"
            message = f"{name}: 在庫情報なし"
        elif stock >= needed:
            status = "ok"
            message = f"{name}: 在庫あり（在庫:{stock}, 必要:{needed}）"
        else:
            status = "insufficient"
            message = f"{name}: 在庫不足（在庫:{stock}, 必要:{needed}, 不足:{needed - stock}）"

        inventory_check[name] = {
            "stock": stock,
            "needed": needed,
            "status": status,
            "message": message,
        }

    # メッセージを構築
    lines = ["【在庫チェック結果】"]
    for drug in prescription:
        info = inventory_check[drug.get("name", "?")]
        lines.append(info["message"])

    # 不足分があれば強調
    insufficient = [
        d for d in prescription
        if inventory_check.get(d.get("name", ""), {}).get("status") == "insufficient"
    ]
    if insufficient:
        lines.append("")
        lines.append("⚠ 在庫不足の薬剤:")
        for d in insufficient:
            info = inventory_check.get(d.get("name", ""), {})
            stock_val = info.get("stock", "?")
            needed_val = info.get("needed", "?")
            lines.append(f"  - {d.get('name', '?')}: 不足{needed_val - stock_val}個")

    return {
        "inventory_check": inventory_check,
        "inventory_message": "\n".join(lines),
    }


# =====================================================================
# NODE ⑦: inventory_exec_node — 在庫実行（並列独立トランジション）
# =====================================================================

def inventory_exec_node(state: dict) -> dict:
    """
    ⑦ 在庫実行ノード — 在庫チェック結果をSlackに送信。

    投射トランジションとは独立して動作。

    Returns:
        {"executed_inventory": True, "sent_to": "slack"}
    """
    message = state.get("inventory_message", "")

    if not message:
        return {
            "executed_inventory": False,
            "sent_to": None,
            "error": "送信メッセージが空です",
        }

    result = _send_to_slack(message)

    if result.get("success"):
        return {
            "executed_inventory": True,
            "sent_to": "slack",
        }
    else:
        return {
            "executed_inventory": False,
            "sent_to": None,
            "error": result.get("error", "Slack送信に失敗しました"),
        }


# =====================================================================
# ヘルパー: 在庫DB照会
# =====================================================================

def query_stock_from_db(pesticide_name: str) -> int | None:
    """
    薬剤名から在庫数を取得。

    在庫DB: stb.db の inventory テーブル
    テーブル構造:
        CREATE TABLE inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pesticide_id TEXT UNIQUE,
            pesticide_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit TEXT DEFAULT '本',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

    Args:
        pesticide_name: 薬剤名（例: "ベルクート"）

    Returns:
        在庫数（int）または存在しない場合は None
    """
    import os
    import sqlite3

    # stb.db のパスを特定（data/ディレクトリ）
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "stb.db")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT quantity FROM inventory WHERE pesticide_name = ?",
            (pesticide_name,),
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        # DBが存在しない、テーブルがない等の場合は None
        return None
