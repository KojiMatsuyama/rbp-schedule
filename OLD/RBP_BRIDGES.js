// RBP_BRIDGES — 横パイプ（橋梁）定義
// LINE間をつなぎ、ENTRY_VECTORに適用される重みベクトル。
// levelが小さいほど上流（Entryに近い）。directionは基本forward。
//
// 各BRIDGEは2つのベクトルを持つ:
//   threshold_vector: 条件判定用。
//                     threshold[i] > 0   → entry[i] >= threshold[i] で满足（高い条件）
//                     threshold[i] < 0   → entry[i] <= -threshold[i] で满足（低い条件）
//                     threshold[i] == 0  → 「無条件」— この次元は判定に関与しない
//   weight_vector:    変換用。Hadamard積で適用。
//                     条件を満たさない限り適用されない。
//                     weight > 1.0 は増幅、weight < 1.0 は減衰

const RBP_BRIDGES = [
  // ── Level 1: EB-02 → EB-07 遷移（5月初頭） ──
  // 炭疽(0)>=0.1 または うどんこ(2)>=0.2 なら発動
  {
    id: "BRIDGE-L1",
    level: 1,
    direction: "forward",
    from_line: "LINE-EB02",
    to_line: "LINE-EB07",
    threshold_vector: [0.1, 0, 0.2, 0, 0, 0, 0, 0, 0, 0],
    weight_vector: [5.0, 0.1, 5.0, 0.01, 0.01, 0.01, 0, 0, 0, 0],
    description: "炭疽>=0.1またはうどんこ>=0.2で発動。"
  },

  // ── Level 2: EB-07 → EB-11 遷移（10月初旬） ──
  // ナミハダニ(3)>=0.8 かつ ハスモン(4)<=0.5 なら EB-11 へ
  // 負の値で「低い条件」を表現: -0.5 = entry[4] <= 0.5
  {
    id: "BRIDGE-L2",
    level: 2,
    direction: "forward",
    from_line: "LINE-EB07",
    to_line: "LINE-EB11",
    threshold_vector: [0, 0, 0, 0.8, -0.5, 0, 0, 0, 0, 0],
    weight_vector: [0.01, 0.01, 0.01, 5.0, 0.01, 0.01, 0, 0, 0, 0],
    description: "ナミ>=0.8かつハスモン<=0.5で発動（ナミハダニ単独優位）。"
  },

  // ── Level 3: EB-07 → EB-12 遷移（10月中下旬） ──
  // ハスモン(4)>=0.9 かつ ナミハダニ(3)<=0.5 なら EB-12 へ
  // 負の値で「低い条件」を表現: -0.5 = entry[3] <= 0.5
  {
    id: "BRIDGE-L3",
    level: 3,
    direction: "forward",
    from_line: "LINE-EB07",
    to_line: "LINE-EB12",
    threshold_vector: [0, 0, 0, -0.5, 0.9, 0, 0, 0, 0, 0],
    weight_vector: [0.01, 0.01, 0.01, 0.01, 5.0, 5.0, 0, 0, 0, 0],
    description: "ハスモン>=0.9かつナミ<=0.5で発動（ハスモン優位）。"
  },

  // ── Level 2.5: EB-07 → EB-11 遷移（複合ケース） ──
  // ナミハダニ(3)>=0.8 かつ ハスモン(4)>=0.8 なら EB-11 へ
  // （EV-060のような複数虫害が同時活性の場合、ナミが優位なら EB-11）
  {
    id: "BRIDGE-L2-5",
    level: 2.5,
    direction: "forward",
    from_line: "LINE-EB07",
    to_line: "LINE-EB11",
    threshold_vector: [0, 0, 0, 0.8, 0.8, 0, 0, 0, 0, 0],
    weight_vector: [0.01, 0.01, 0.01, 5.0, 0.01, 0.01, 0, 0, 0, 0],
    description: "ナミ>=0.8かつハスモン>=0.8で発動（ナミが優位な複合ケース）。"
  },

  // ── Level 4: EB-11 → EB-12 遷移（10月内） ──
  // ハスモン(4)>=0.9 かつ オオタバコガ(5)>=0.9 なら EB-12 へ
  {
    id: "BRIDGE-L4",
    level: 4,
    direction: "forward",
    from_line: "LINE-EB11",
    to_line: "LINE-EB12",
    threshold_vector: [0, 0, 0, 0, 0.9, 0.9, 0, 0, 0, 0],
    weight_vector: [0.01, 0.01, 0.01, 0.01, 5.0, 5.0, 0, 0, 0, 0],
    description: "ハスモン>=0.9かつオオタ>=0.9で発動（両者併存）。"
  },

  // ── Level 4: EB-07 → EB-02 再帰（11月収束） [DISABLED] ──
  // 炭疽<0.1 かつ うどんこ<0.1 で発動する条件が不明確。
  // 現在は entry >= threshold で判定するため、条件を満たすことはない。
  // 11月データが1件のみのため、一般化不可。
  /*
  {
    id: "BRIDGE-L5",
    level: 4,
    direction: "forward",
    from_line: "LINE-EB07",
    to_line: "LINE-EB02",
    threshold_vector: [0.1, 0, 0.1, 0, 0, 0, 0, 0, 0, 0],
    weight_vector: [0.01, 5.0, 0.01, 0.01, 0, 0, 0, 0, 0, 0],
    description: "炭疽<0.1かつうどんこ<0.1で発動。灰色かびを5倍増幅。"
  }
  */
];

// 病害虫インデックス（10次元）:
//   0: 炭疽病   1: 灰色かび病   2: うどんこ病   3: ナミハダニ
//   4: ハスモンヨトウ 5: オオタバコガ 6: ミカンキイロアザミウマ
//   7: ワタアブラムシ 8: アブラムシ   9: コナジラミ

module.exports = { RBP_BRIDGES };
