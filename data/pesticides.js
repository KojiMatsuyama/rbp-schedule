// data/pesticides.js — 薬剤仕様データベース（PESTICIDE_DB, 67剤）
// アプリケーション固有データ。10次元ベクトル空間はdata/diseases.jsのDISEASESと同一。
// インデックス: 0:炭疽病 1:灰色かび病 2:うどんこ病 3:ナミハダニ 4:ハスモンヨトウ
//              5:オオタバコガ 6:ミカンキイロアザミウマ 7:ワタアブラムシ 8:アブラムシ 9:コナジラミ

const PESTICIDE_DB = [
  // ── 殺菌剤（病害） ──
  { id:"P01", name:"ベルクート", activeIngredient:"アゾキシストロビン", category:"fungicide",
    targetVector:[1,1,1,0,0,0,0,0,0,0], targetNames:["炭疽","うどんこ","灰色かび"],
    phiDays:1, mixingRestriction:"銅剤と混合不可", mixingBanTargets:["銅剤"],
    maxApplications:3, toxicityClass:"普通物", system:"QoI（ストロビルリン）", systemCode:"FRAC11" },

  { id:"P02", name:"キノンドー", activeIngredient:"キノキサリン系", category:"fungicide",
    targetVector:[1,0,0,0,0,0,0,0,0,0], targetNames:["炭疽"],
    phiDays:1, mixingRestriction:"酸性剤、銅剤、硫黄剤と混合不可", mixingBanTargets:["酸性剤","銅剤","硫黄剤"],
    maxApplications:2, toxicityClass:"劇物", system:"キノキサリン系", systemCode:"FRAC-QUINOX" },

  { id:"P03", name:"ゲッター", activeIngredient:"フルアジナム", category:"fungicide",
    targetVector:[1,0,0,0,0,0,0,0,0,0], targetNames:["炭疽"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"フルアジナム系", systemCode:"FRAC29" },

  { id:"P04", name:"ランマン", activeIngredient:"シアゾファミド", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"銅剤と混合注意", mixingBanTargets:["銅剤"],
    maxApplications:2, toxicityClass:"普通物", system:"チアゾリジン系", systemCode:"FRAC21" },

  { id:"P05", name:"アントラコール", activeIngredient:"プロピネブ", category:"fungicide",
    targetVector:[1,0,1,0,0,0,0,0,0,0], targetNames:["炭疽","うどんこ"],
    phiDays:1, mixingRestriction:"銅剤と混合不可", mixingBanTargets:["銅剤"],
    maxApplications:3, toxicityClass:"普通物", system:"ジチオカーバメート系", systemCode:"FRACM3" },

  { id:"P06", name:"ストロビー", activeIngredient:"クレソキシムメチル", category:"fungicide",
    targetVector:[1,0,1,0,0,0,0,0,0,0], targetNames:["炭疽","うどんこ"],
    phiDays:1, mixingRestriction:"銅剤と混合不可", mixingBanTargets:["銅剤"],
    maxApplications:3, toxicityClass:"普通物", system:"QoI", systemCode:"FRAC11" },

  { id:"P07", name:"パンチョ", activeIngredient:"フルアジナム＋他", category:"fungicide",
    targetVector:[1,0,0,0,0,0,0,0,0,0], targetNames:["炭疽"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"混合剤", systemCode:"MIX" },

  { id:"P08", name:"シグナム", activeIngredient:"ボスカリド＋ピラクロストロビン", category:"fungicide",
    targetVector:[1,1,1,0,0,0,0,0,0,0], targetNames:["灰色かび","うどんこ","炭疽"],
    phiDays:1, mixingRestriction:"銅剤と混合不可", mixingBanTargets:["銅剤"],
    maxApplications:2, toxicityClass:"普通物", system:"SDHI＋QoI", systemCode:"FRAC7+11" },

  { id:"P09", name:"ファンタジスタ", activeIngredient:"ピラクロストロビン", category:"fungicide",
    targetVector:[1,0,1,0,0,0,0,0,0,0], targetNames:["炭疽","うどんこ"],
    phiDays:1, mixingRestriction:"銅剤と混合不可", mixingBanTargets:["銅剤"],
    maxApplications:3, toxicityClass:"普通物", system:"QoI", systemCode:"FRAC11" },

  { id:"P10", name:"ダブルフェース", activeIngredient:"シエノピラン", category:"fungicide",
    targetVector:[0,0,1,0,0,0,0,0,0,0], targetNames:["うどんこ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"ピラゾール系", systemCode:"FRAC-PYRAZOLE" },

  { id:"P11", name:"トレノックス", activeIngredient:"テブコナゾール", category:"fungicide",
    targetVector:[0,0,1,0,0,0,0,0,0,0], targetNames:["うどんこ"],
    phiDays:1, mixingRestriction:"銅剤と混合注意", mixingBanTargets:["銅剤"],
    maxApplications:2, toxicityClass:"普通物", system:"DMI（トリアゾール）", systemCode:"FRAC3" },

  { id:"P12", name:"レーバス", activeIngredient:"マンジプロパミド", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"マンジプロパミド系", systemCode:"FRAC40" },

  { id:"P13", name:"プレバソン", activeIngredient:"イミノクタジン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"展着剤過剰注意", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"イミノクタジン系", systemCode:"FRAC-GUANIDINE" },

  { id:"P14", name:"プロパティ", activeIngredient:"プロピコナゾール", category:"fungicide",
    targetVector:[0,0,1,0,0,0,0,0,0,0], targetNames:["うどんこ"],
    phiDays:1, mixingRestriction:"銅剤注意", mixingBanTargets:["銅剤"],
    maxApplications:2, toxicityClass:"普通物", system:"DMI", systemCode:"FRAC3" },

  { id:"P15", name:"ベネピア", activeIngredient:"ベノミル", category:"fungicide",
    targetVector:[0,1,1,0,0,0,0,0,0,0], targetNames:["うどんこ","灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:3, toxicityClass:"普通物", system:"ベンズイミダゾール系", systemCode:"FRAC1" },

  { id:"P16", name:"ファンベル", activeIngredient:"フルジオキソニル", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"フェニルピロール", systemCode:"FRAC12" },

  { id:"P17", name:"カナメ", activeIngredient:"メパニピリム", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"アニリド系", systemCode:"FRAC-ANILIDE" },

  { id:"P18", name:"アフェット", activeIngredient:"イミノクタジン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"展着剤過剰注意", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"イミノクタジン系", systemCode:"FRAC-GUANIDINE" },

  { id:"P19", name:"アベンジャー", activeIngredient:"イプロジオン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"ジカルボキシイミド系", systemCode:"FRAC2" },

  { id:"P20", name:"カウンター", activeIngredient:"キャプタン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"アルカリ剤と混合不可", mixingBanTargets:["アルカリ剤"],
    maxApplications:3, toxicityClass:"普通物", system:"フタルイミド系", systemCode:"FRACM4" },

  { id:"P21", name:"ジーファイン", activeIngredient:"チオファネートメチル", category:"fungicide",
    targetVector:[0,1,1,0,0,0,0,0,0,0], targetNames:["うどんこ","灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:3, toxicityClass:"普通物", system:"ベンズイミダゾール系", systemCode:"FRAC1" },

  { id:"P22", name:"サンクリ", activeIngredient:"イミノクタジン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"展着剤過剰注意", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"イミノクタジン系", systemCode:"FRAC-GUANIDINE" },

  { id:"P23", name:"ヨーバル", activeIngredient:"イミノクタジン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"展着剤過剰注意", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"イミノクタジン系", systemCode:"FRAC-GUANIDINE" },

  { id:"P24", name:"グレーシア", activeIngredient:"イミノクタジン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"展着剤過剰注意", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"イミノクタジン系", systemCode:"FRAC-GUANIDINE" },

  { id:"P25", name:"フルピカ", activeIngredient:"フルジオキソニル", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"フェニルピロール", systemCode:"FRAC12" },

  { id:"P26", name:"オラクル水和剤", activeIngredient:"イミノクタジン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"展着剤過剰注意", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"イミノクタジン系", systemCode:"FRAC-GUANIDINE" },

  { id:"P27", name:"トリフミン", activeIngredient:"トリフルミゾール", category:"fungicide",
    targetVector:[0,0,1,0,0,0,0,0,0,0], targetNames:["うどんこ"],
    phiDays:1, mixingRestriction:"銅剤注意", mixingBanTargets:["銅剤"],
    maxApplications:2, toxicityClass:"普通物", system:"DMI", systemCode:"FRAC3" },

  { id:"P28", name:"セイピア", activeIngredient:"ピリベンカルブ", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"カルバメート系", systemCode:"FRAC-CARBAMATE" },

  { id:"P29", name:"ファインセーブフロアブル", activeIngredient:"フルジオキソニル", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"フェニルピロール", systemCode:"FRAC12" },

  { id:"P30", name:"イオウフロアブル", activeIngredient:"硫黄剤", category:"fungicide",
    targetVector:[0,0,1,0,0,0,0,0,0,0], targetNames:["うどんこ"],
    phiDays:1, mixingRestriction:"油剤・サフオイルと混合不可", mixingBanTargets:["油剤","サフオイル"],
    maxApplications:Infinity, toxicityClass:"普通物", system:"無機硫黄", systemCode:"FRACM2" },

  { id:"P31", name:"カリグリーン", activeIngredient:"炭酸水素カリウム", category:"fungicide",
    targetVector:[0,0,1,0,0,0,0,0,0,0], targetNames:["うどんこ"],
    phiDays:1, mixingRestriction:"酸性剤と混合不可", mixingBanTargets:["酸性剤"],
    maxApplications:Infinity, toxicityClass:"普通物", system:"無機塩", systemCode:"FRAC-BICARB" },

  { id:"P32", name:"ショウチノスケフロアブル", activeIngredient:"イミノクタジン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"展着剤過剰注意", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"イミノクタジン系", systemCode:"FRAC-GUANIDINE" },

  { id:"P33", name:"スコア顆粒水和剤", activeIngredient:"ジフェノコナゾール", category:"fungicide",
    targetVector:[0,0,1,0,0,0,0,0,0,0], targetNames:["うどんこ"],
    phiDays:1, mixingRestriction:"銅剤注意", mixingBanTargets:["銅剤"],
    maxApplications:2, toxicityClass:"普通物", system:"DMI（トリアゾール）", systemCode:"FRAC3" },

  { id:"P34", name:"セイビアーフロアブル20", activeIngredient:"フルアジナム", category:"fungicide",
    targetVector:[1,0,0,0,0,0,0,0,0,0], targetNames:["炭疽"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"フルアジナム系", systemCode:"FRAC29" },

  { id:"P35", name:"ハーモメイト水溶剤", activeIngredient:"キャプタン", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"アルカリ剤と混合不可", mixingBanTargets:["アルカリ剤"],
    maxApplications:3, toxicityClass:"普通物", system:"フタルイミド系", systemCode:"FRACM4" },

  { id:"P36", name:"パンチョTF顆粒水和剤", activeIngredient:"フルアジナム＋他", category:"fungicide",
    targetVector:[1,0,0,0,0,0,0,0,0,0], targetNames:["炭疽"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"混合剤", systemCode:"MIX" },

  { id:"P37", name:"ラミック顆粒水和剤", activeIngredient:"マンジプロパミド", category:"fungicide",
    targetVector:[0,1,0,0,0,0,0,0,0,0], targetNames:["灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"マンジプロパミド系", systemCode:"FRAC40" },

  { id:"P38", name:"ベンレート", activeIngredient:"ベノミル", category:"fungicide",
    targetVector:[0,1,1,0,0,0,0,0,0,0], targetNames:["うどんこ","灰色かび"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:3, toxicityClass:"普通物", system:"ベンズイミダゾール系（FRAC 1）", systemCode:"FRAC1" },

  { id:"P39", name:"アミスタ", activeIngredient:"アゾキシストロビン", category:"fungicide",
    targetVector:[0,1,1,0,0,0,0,0,0,0], targetNames:["灰色かび","うどんこ"],
    phiDays:1, mixingRestriction:"銅剤と混合不可（QoI系は銅で分解）", mixingBanTargets:["銅剤"],
    maxApplications:3, toxicityClass:"普通物", system:"QoI（ストロビルリン系／FRAC 11）", systemCode:"FRAC11" },

  { id:"P40", name:"コサイド300", activeIngredient:"銅水和剤", category:"fungicide",
    targetVector:[1,0,0,0,0,0,0,0,0,0], targetNames:["炭疽","斑点病","細菌病"],
    phiDays:1, mixingRestriction:"酸性剤と混合不可（沈殿・薬害）", mixingBanTargets:["酸性剤"],
    maxApplications:Infinity, toxicityClass:"普通物", system:"無機銅剤（FRAC M1）", systemCode:"FRACM1" },

  // ── 殺虫剤（害虫） ──
  { id:"P41", name:"アファーム", activeIngredient:"エマメクチン", category:"insecticide",
    targetVector:[0,0,0,0,1,1,0,0,0,0], targetNames:["ハスモンヨトウ","オオタバコガ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"マクロライド", systemCode:"IRAC6" },

  { id:"P42", name:"コテツ", activeIngredient:"スピノサド", category:"insecticide",
    targetVector:[0,0,0,0,0,1,1,0,0,0], targetNames:["アザミウマ","オオタバコガ"],
    phiDays:1, mixingRestriction:"アルカリ剤と混合不可", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"スピノシン", systemCode:"IRAC5" },

  { id:"P43", name:"プレオ", activeIngredient:"ピメトロジン", category:"insecticide",
    targetVector:[0,0,0,0,0,0,0,0,1,0], targetNames:["アブラムシ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"ピリジン系", systemCode:"IRAC9B" },

  { id:"P44", name:"チェス", activeIngredient:"ピメトロジン", category:"insecticide",
    targetVector:[0,0,0,0,0,0,0,0,1,0], targetNames:["アブラムシ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"ピリジン系", systemCode:"IRAC9B" },

  { id:"P45", name:"トルネード", activeIngredient:"クロルフェナピル", category:"insecticide",
    targetVector:[0,0,0,0,1,1,0,0,0,0], targetNames:["ハスモンヨトウ","オオタバコガ"],
    phiDays:1, mixingRestriction:"アルカリ剤と混合不可", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"フェニルピロール", systemCode:"FRAC12" },

  { id:"P46", name:"スピノエース", activeIngredient:"スピノサド", category:"insecticide",
    targetVector:[0,0,0,0,0,1,1,0,0,0], targetNames:["アザミウマ","オオタバコガ"],
    phiDays:1, mixingRestriction:"アルカリ剤と混合不可", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"スピノシン", systemCode:"IRAC5" },

  { id:"P47", name:"ラリー", activeIngredient:"ピリダベン", category:"insecticide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"アルカリ剤注意", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"METI", systemCode:"IRAC21A" },

  { id:"P48", name:"トクチオン", activeIngredient:"クロルピリホス", category:"insecticide",
    targetVector:[0,0,0,0,0,0,0,0,1,0], targetNames:["アブラムシ"],
    phiDays:7, mixingRestriction:"多数（有機リン系）", mixingBanTargets:["有機リン系"],
    maxApplications:1, toxicityClass:"劇物", system:"有機リン", systemCode:"IRAC1B" },

  { id:"P49", name:"アグリメック", activeIngredient:"アバメクチン", category:"insecticide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"マクロライド", systemCode:"IRAC6" },

  { id:"P50", name:"ベストガード", activeIngredient:"クロチアニジン", category:"insecticide",
    targetVector:[0,0,0,0,0,0,0,0,1,1], targetNames:["アブラムシ","コナジラミ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"ネオニコチノイド", systemCode:"IRAC4A" },

  { id:"P51", name:"ムシラップ", activeIngredient:"クロチアニジン", category:"insecticide",
    targetVector:[0,0,0,0,0,0,0,0,1,1], targetNames:["アブラムシ","コナジラミ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"ネオニコチノイド", systemCode:"IRAC4A" },

  { id:"P52", name:"モスピラン顆粒水溶剤", activeIngredient:"アセタミプリド", category:"insecticide",
    targetVector:[0,0,0,0,0,0,0,0,1,1], targetNames:["アブラムシ","コナジラミ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"ネオニコチノイド", systemCode:"IRAC4A" },

  // ── 殺ダニ剤（害虫） ──
  { id:"P53", name:"コロマイト", activeIngredient:"フェンピロキシメート", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"METI", systemCode:"IRAC21A" },

  { id:"P54", name:"スターマイト", activeIngredient:"テトラジホン", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"アルカリ剤注意", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"テトラジホン系", systemCode:"IRAC-TETRADIFON" },

  { id:"P55", name:"バリアード", activeIngredient:"ヘキサチアゾクス", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"IGR（卵阻害）", systemCode:"IRAC10" },

  { id:"P56", name:"ダニサラバ", activeIngredient:"ミルベメクチン", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"マクロライド", systemCode:"IRAC6" },

  { id:"P57", name:"ダニコング", activeIngredient:"ミルベメクチン", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"マクロライド", systemCode:"IRAC6" },

  { id:"P58", name:"マイトコーネ", activeIngredient:"テトラジホン", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"アルカリ剤注意", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"テトラジホン系", systemCode:"IRAC-TETRADIFON" },

  { id:"P59", name:"アーデント水和剤", activeIngredient:"ピリダベン", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"アルカリ剤注意", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"METI", systemCode:"IRAC21A" },

  { id:"P60", name:"ウララDF", activeIngredient:"フルフェノクスロン", category:"insecticide",
    targetVector:[0,0,0,0,1,0,0,0,0,0], targetNames:["ハスモンヨトウ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"IGR", systemCode:"IRAC10" },

  { id:"P61", name:"サフオイル", activeIngredient:"精製植物油", category:"acaricide",
    targetVector:[0,0,0,1,0,0,1,0,0,0], targetNames:["ハダニ","アザミウマ"],
    phiDays:1, mixingRestriction:"硫黄剤と混合不可", mixingBanTargets:["硫黄剤"],
    maxApplications:Infinity, toxicityClass:"普通物", system:"物理剤", systemCode:"PHYSICAL" },

  { id:"P62", name:"ディアナSC", activeIngredient:"スピロメシフェン", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"脂質生合成阻害（IRAC 23）", systemCode:"IRAC23" },

  { id:"P63", name:"ニッソラン水和剤", activeIngredient:"ヘキサチアゾクス", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"特記事項なし", mixingBanTargets:[],
    maxApplications:2, toxicityClass:"普通物", system:"IGR（卵阻害）", systemCode:"IRAC10" },

  { id:"P64", name:"バロックフロアブル", activeIngredient:"クロルフェナピル", category:"insecticide",
    targetVector:[0,0,0,0,1,0,0,0,0,0], targetNames:["ハスモンヨトウ"],
    phiDays:1, mixingRestriction:"アルカリ剤と混合不可", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"フェニルピロール", systemCode:"FRAC12" },

  { id:"P65", name:"オルフィンフロアブル", activeIngredient:"ピリダベン", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"アルカリ剤注意", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"METI", systemCode:"IRAC21A" },

  { id:"P66", name:"ガッテン乳剤", activeIngredient:"クロルフェナピル", category:"insecticide",
    targetVector:[0,0,0,0,1,0,0,0,0,0], targetNames:["ハスモンヨトウ"],
    phiDays:1, mixingRestriction:"アルカリ剤と混合不可", mixingBanTargets:["アルカリ剤"],
    maxApplications:2, toxicityClass:"普通物", system:"フェニルピロール", systemCode:"FRAC12" },

  { id:"P67", name:"ダニオーテフロアブル", activeIngredient:"アシノナピル", category:"acaricide",
    targetVector:[0,0,0,1,0,0,0,0,0,0], targetNames:["ハダニ"],
    phiDays:1, mixingRestriction:"銅剤と混合不可（薬害・分解）", mixingBanTargets:["銅剤"],
    maxApplications:2, toxicityClass:"普通物", system:"METI（複合体Ⅱ阻害／IRAC 21A）", systemCode:"IRAC21A" },
];
