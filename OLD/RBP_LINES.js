// RBP_LINES — 縦パイプ（ライン）定義
// 各ラインは1つの評価BOXへ向かう意味流の主流路。
// ENTRY_VECTOR はレベル順にBRIDGEを通過し、最終的に到達したラインの
// 評価BOXが要求評価の結果となる。

const RBP_LINES = [
  {
    id: "LINE-EB02",
    eval_box_id: "EB-02",
    eval_vector: [0, 1, 0, 0, 0, 0, 0, 0, 0, 0],
    description: "灰色かび病ライン（早春・晩秋の単一病害フロー）",
    entry_count: 10,              // EV-001〜007, EV-065
    date_range: "2/21 〜 4/18, 11/4"
  },
  {
    id: "LINE-EB07",
    eval_box_id: "EB-07",
    eval_vector: [1, 1, 1, 0, 0, 0, 0, 0, 0, 0],
    description: "炭疽+うどんこ+灰色かびライン（主力複合病害フロー）",
    entry_count: 50,              // EV-008〜057
    date_range: "5/3 〜 9/29"
  },
  {
    id: "LINE-EB11",
    eval_box_id: "EB-11",
    eval_vector: [1, 1, 1, 1, 0, 0, 0, 0, 0, 0],
    description: "炭疽+うどんこ+灰色かび+ナミハダニライン（10月上旬）",
    entry_count: 5,               // EV-058, EV-060〜063
    date_range: "10/2, 10/8 〜 10/24"
  },
  {
    id: "LINE-EB12",
    eval_box_id: "EB-12",
    eval_vector: [1, 1, 1, 0, 1, 1, 0, 0, 0, 0],
    description: "炭疽+うどんこ+灰色かび+ハスモン+オオタライン（10月中下旬）",
    entry_count: 2,               // EV-059, EV-064
    date_range: "10/6, 10/28"
  }
];

// 病害虫インデックス（10次元）:
//   0: 炭疽病   1: 灰色かび病   2: うどんこ病   3: ナミハダニ
//   4: ハスモンヨトウ 5: オオタバコガ 6: ミカンキイロアザミウマ
//   7: ワタアブラムシ 8: アブラムシ   9: コナジラミ

module.exports = { RBP_LINES };
