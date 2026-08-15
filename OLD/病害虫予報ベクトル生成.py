#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
病害虫予報ベクトル (ENTRY_VECTOR) 生成スクリプト

防除カレンダーの散布履歴から、各散布日の病害虫リスク値ベクトル [0,1]^10 を生成する。

 pest_index (10 dimensions):
   0: 炭疽病
   1: 灰色かび病
   2: うどんこ病
   3: ナミハダニ
   4: ハスモンヨトウ
   5: オオタバコガ
   6: ミカンキイロアザミウマ
   7: ワタアブラムシ
   8: アブラムシ
   9: コナジラミ
"""

import json
import re
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# 1. 病害虫インデックス定義
# ============================================================
PEST_INDEX = [
    "炭疽病",
    "灰色かび病",
    "うどんこ病",
    "ナミハダニ",
    "ハスモンヨトウ",
    "オオタバコガ",
    "ミカンキイロアザミウマ",
    "ワタアブラムシ",
    "アブラムシ",
    "コナジラミ",
]
N_DIM = len(PEST_INDEX)  # 10

# ============================================================
# 2. 防除カレンダーのパース
# ============================================================
def parse_calendar(filepath):
    """
    防除カレンダー.txt をパースし、散布履歴を抽出する。

    戻り値: list of dict
      [
        {"date": "2/21", "pests": ["うどんこ病", "灰色かび病"], "set": 6},
        ...
      ]
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    records = []
    current_month = None

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # 月のヘッダー
        month_match = re.match(r"^(?:(\d+)月|(\d+)-?月)$", line)
        if month_match:
            current_month = int(month_match.group(1) or month_match.group(2))
            continue

        # 日付行 (例: 2/21, 3/7)
        date_match = re.match(r"^(\d+)/(\d+)$", line)
        if date_match:
            month = current_month
            day = int(date_match.group(1))
            day = int(date_match.group(2))
            date_str = f"{month}/{day}"
            continue

        # 病害虫行 (例: うどんこ病、灰色かび病)
        if date_str and "→" not in line:
            pests = [p.strip() for p in line.split("、")]
            continue

        # セット行 (例: → セット6)
        if date_str and "→" in line:
            set_match = re.search(r"セット(\d+)", line)
            if set_match:
                set_num = int(set_match.group(1))
                records.append({
                    "date": date_str,
                    "month": month,
                    "day": day,
                    "pests": pests,
                    "set": set_num,
                })
                date_str = None
                pests = None

    return records


# ============================================================
# 3. 防除カタログ — セット番号 → 病害虫マッピング
# ============================================================
SET_TO_PESTS = {
    1:  ["炭疽病"],
    2:  ["灰色かび病"],
    3:  ["うどんこ病"],
    4:  ["炭疽病", "うどんこ病"],
    5:  ["炭疽病", "灰色かび病"],
    6:  ["うどんこ病", "灰色かび病"],
    7:  ["炭疽病", "うどんこ病", "灰色かび病"],
    8:  ["灰色かび病", "ナミハダニ"],
    9:  ["炭疽病", "ナミハダニ"],
    10: ["うどんこ病", "ナミハダニ"],
    11: ["炭疽病", "うどんこ病", "灰色かび病", "ナミハダニ"],
    12: ["炭疽病", "うどんこ病", "灰色かび病", "ハスモンヨトウ", "オオタバコガ"],
    13: ["灰色かび病", "ハスモンヨトウ", "オオタバコガ"],
    14: ["うどんこ病", "ミカンキイロアザミウマ", "オオタバコガ"],
    15: ["炭疽病", "ハスモンヨトウ", "オオタバコガ"],
    16: ["炭疽病", "うどんこ病", "ハスモンヨトウ", "オオタバコガ", "ミカンキイロアザミウマ"],
    17: ["炭疽病", "ワタアブラムシ"],
    18: ["灰色かび病", "うどんこ病", "ナミハダニ"],
    19: ["灰色かび病", "うどんこ病", "ハスモンヨトウ"],
    20: ["灰色かび病", "うどんこ病", "ナミハダニ", "ワタアブラムシ"],
    21: ["炭疽病", "うどんこ病", "灰色かび病", "ナミハダニ", "ハスモンヨトウ", "オオタバコガ"],
    22: ["炭疽病", "うどんこ病", "灰色かび病", "ハスモンヨトウ", "オオタバコガ", "アブラムシ", "コナジラミ"],
}


# ============================================================
# 4. 薬剤DB — 農薬名 → 対象病害虫マッピング
# ============================================================
# 薬剤DB.txt から手動で抽出した農薬リスト
# 各農薬が対象とする病害虫を定義
PESTICIDE_DB = {
    # --- 殺菌剤 ---
    "ベルクート": {"炭疽病", "うどんこ病", "灰色かび病"},
    "キノンドー": {"炭疽病"},
    "ゲッター": {"炭疽病"},
    "ランマン": {"灰色かび病"},
    "アントラコール": {"炭疽病", "うどんこ病"},
    "ストロビー": {"炭疽病", "うどんこ病"},
    "パンチョ": {"炭疽病"},
    "シグナム": {"灰色かび病", "うどんこ病", "炭疽病"},
    "ファンタジスタ": {"炭疽病", "うどんこ病"},
    "ダブルフェース": {"うどんこ病"},
    "トレノックス": {"うどんこ病"},
    "レーバス": {"灰色かび病"},
    "プレバソン": {"灰色かび病"},
    "バミューダ": {"灰色かび病"},
    "ダコニール1000": {"炭疽病", "うどんこ病", "灰色かび病"},
    "ulfite": {"うどんこ病"},
    "カリミット": {"うどんこ病"},
    "速保富": {"うどんこ病"},
    "アミスター28": {"炭疽病", "灰色かび病"},
    "フィキサート": {"炭疽病", "うどんこ病", "灰色かび病"},
    "オルトラン水和剤": {"ナミハダニ", "ハスモンヨトウ", "オオタバコガ", "ミカンキイロアザミウマ", "ワタアブラムシ", "アブラムシ", "コナジラミ"},
    "アファーム": {"ハスモンヨトウ", "オオタバコガ"},
    "コロマイト": {"ナミハダニ"},
    "ダノxon": {"アブラムシ", "コナジラミ"},
    "アクテリック": {"ナミハダニ"},
    "ピリオド": {"ナミハダニ"},
    "ダノクス": {"アブラムシ", "コナジラミ"},
    "ネムノキ油": {"アブラムシ", "コナジラミ"},
    "スミチオン": {"アブラムシ", "コナジラミ", "ハスモンヨトウ"},
    "ピレスロirin": {"アブラムシ", "コナジラミ", "ハスモンヨトウ", "オオタバコガ"},
    "インターコンドル": {"ミカンキイロアザミウマ", "ハスモンヨトウ"},
    "アセテップ": {"オオタバコガ", "ハスモンヨトウ"},
    "ラベルト": {"オオタバコガ", "ハスモンヨトウ"},
    "アディオン": {"ワタアブラムシ", "アブラムシ"},
    "モスピラン": {"アブラムシ", "コナジラミ", "ミカンキイロアザミウマ"},
    "ダントツ": {"ミカンキイロアザミウマ", "アブラムシ", "コナジラミ"},
    "コンフィード": {"アブラムシ", "コナジラミ", "ミカンキイロアザミウマ"},
    "ノワール": {"ナミハダニ"},
    "マイトコン": {"ナミハダニ"},
    "タフト": {"ナミハダニ"},
    "デナホート": {"うどんこ病"},
    "スミデップ": {"アブラムシ", "コナジラミ"},
    "カスレイン": {"灰色かび病"},
    "トップジンM": {"炭疽病", "うどんこ病"},
    "ダイセン": {"炭疽病", "うどんこ病", "灰色かび病"},
    "マンネバ": {"灰色かび病"},
    "ボルドー": {"炭疽病", "うどんこ病", "灰色かび病"},
    "石灰硫黄合剤": {"ナミハダニ", "うどんこ病", "炭疽病"},
}


# ============================================================
# 5. セット番号 → 使用農薬の推定
# ============================================================
def infer_pesticides_for_set(set_num):
    """
    セット番号から、使用されたと推定される農薬リストを返す。

    各病害虫に対して、薬剤DBから対象農薬を抽出し、
    そのセットでカバーされる病害虫すべてをカバーする農薬の組み合わせを推定。
    """
    if set_num not in SET_TO_PESTS:
        return []

    target_pests = SET_TO_PESTS[set_num]
    candidate_pesticides = []

    for pesticide_name, targets in PESTICIDE_DB.items():
        # この農薬がセットの対象病害虫の少なくとも1つをカバーすれば候補
        covered = targets & set(target_pests)
        if covered:
            candidate_pesticides.append({
                "name": pesticide_name,
                "targets": targets,
                "covered_in_set": covered,
                "coverage_ratio": len(covered) / len(target_pests),
            })

    return candidate_pesticides


# ============================================================
# 6. リスク値計算
# ============================================================
def calculate_risk_values(spray_records):
    """
    散布履歴から各散布日のENTRY_VECTORを計算する。

    リスク値の計算方法:
    - 各散布日について、その日に使用されたセットの病害虫を特定
    - 各病害虫について、そのセットで使用されたと推定される農薬の数をカウント
    - リスク値 = (その病害虫をカバーする農薬数) / (その病害虫をカバーする全農薬数)
    - 最近の散布ほどリスクが高くなるよう、時間減衰を適用
    """
    # 全散布日のリスト
    entry_vectors = []

    for record in spray_records:
        set_num = record["set"]
        target_pests = SET_TO_PESTS.get(set_num, [])
        if not target_pests:
            continue

        # 各病害虫のリスク値を初期化
        risk = [0.0] * N_DIM

        # このセットで使用されたと推定される農薬を取得
        pesticides = infer_pesticides_for_set(set_num)

        # 各農薬の効果に基づいてリスク値を加算
        for pest_idx, pest_name in enumerate(PEST_INDEX):
            if pest_name not in target_pests:
                continue

            # この病害虫をカバーする農薬の数
            covering_pesticides = [
                p for p in pesticides
                if pest_name in p["targets"]
            ]

            if covering_pesticides:
                # リスク値 = カバーする農薬数 / 全農薬数（正規化）
                # または、単純に農薬数に基づいて重み付け
                risk[pest_idx] = min(1.0, len(covering_pesticides) / 5.0)

        # 時間減衰係数を適用（最近の散布ほどリスクが高い）
        # 散布日を年月日でパース
        date_str = record["date"]
        try:
            spray_date = datetime(2025, record["month"], record["day"])
            # 基準日（最終散布日）からの日数差
            days_since = (datetime(2025, 11, 4) - spray_date).days
            decay_factor = max(0.3, 1.0 - days_since / 365.0)
            risk = [r * decay_factor for r in risk]
        except:
            pass

        entry_vectors.append({
            "date": date_str,
            "set": set_num,
            "pests": target_pests,
            "vector": [round(r, 3) for r in risk],
            "pesticide_count": len(pesticides),
        })

    return entry_vectors


# ============================================================
# 7. 出力
# ============================================================
def main():
    # 防除カレンダーをパース
    calendar_file = "防除カレンダー.txt"
    records = parse_calendar(calendar_file)

    print(f"=== 散布履歴パース結果 ===")
    print(f"総散布回数: {len(records)}")
    print()

    # ENTRY_VECTOR を計算
    entry_vectors = calculate_risk_values(records)

    # JSON 出力
    output = {
        "meta": {
            "description": "病害虫予報ベクトル (ENTRY_VECTOR)",
            "dimensions": N_DIM,
            "pest_index": PEST_INDEX,
            "total_sprays": len(records),
            "generation_method": "散布履歴から農薬効果を抽出し、重み付けリスク値 [0,1] を生成",
        },
        "entry_vectors": entry_vectors,
    }

    # ファイル出力
    output_file = "病害虫予報ベクトル生成結果.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"=== ENTRY_VECTOR 生成完了 ===")
    print(f"出力ファイル: {output_file}")
    print()

    # サンプル表示（最初5件）
    print("=== サンプル（最初5件） ===")
    for ev in entry_vectors[:5]:
        print(f"\n{ev['date']} (セット{ev['set']})")
        print(f"  対象病害虫: {ev['pests']}")
        print(f"  農薬数: {ev['pesticide_count']}")
        print(f"  ベクトル: {ev['vector']}")

    # 統計情報
    print(f"\n=== 統計情報 ===")
    print(f"総散布回数: {len(entry_vectors)}")

    # 各病害虫の平均リスク値
    avg_risk = [0.0] * N_DIM
    for ev in entry_vectors:
        for i in range(N_DIM):
            avg_risk[i] += ev["vector"][i]
    avg_risk = [r / len(entry_vectors) for r in avg_risk]

    print("\n各病害虫の平均リスク値:")
    for i, pest in enumerate(PEST_INDEX):
        print(f"  {pest}: {avg_risk[i]:.3f}")

    # 最高リスクの散布日
    max_risk_date = max(entry_vectors, key=lambda x: sum(x["vector"]))
    print(f"\n最高リスクの散布日: {max_risk_date['date']} (セット{max_risk_date['set']})")
    print(f"  合計リスク: {sum(max_risk_date['vector']):.3f}")


if __name__ == "__main__":
    main()
