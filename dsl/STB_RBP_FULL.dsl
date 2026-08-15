Domain STB {
  dimensions: 10;
  lines: ["炭疽病","灰色かび病","うどんこ病","輪斑病","ハスモンヨトウ",
          "オオタバコガ","ミカンキイロアザミウマ","ワタアブラムシ",
          "アブラムシ","コナジラミ"];
}

Demand-Boundary PestDemand {
  Line-0: 炭疽病(disease);
  Line-1: 灰色かび病(disease);
  Line-2: うどんこ病(disease);
  Line-3: 輪斑病(disease);
  Line-4: ハスモンヨトウ(pest);
  Line-5: オオタバコガ(pest);
  Line-6: ミカンキイロアザミウマ(pest);
  Line-7: ワタアブラムシ(pest);
  Line-8: アブラムシ(pest);
  Line-9: コナジラミ(pest);
  Demand-Rule { on-occurrence => Line-i = 1; on-absence => Line-i = 0; }
}

Bridge-Boundary EvalBox {
  BOX-01: { Line-0 } as "炭疽病";
  BOX-02: { Line-1 } as "灰色かび病";
  BOX-03: { Line-2 } as "うどんこ病";
  BOX-04: { Line-0,Line-2 } as "炭疽病+うどんこ病";
  BOX-05: { Line-0,Line-1 } as "炭疽病+灰色かび病";
  BOX-06: { Line-1,Line-2 } as "灰色かび病+うどんこ病";
  BOX-07: { Line-0,Line-1,Line-2 } as "炭疽病+灰色かび病+うどんこ病";
  BOX-08: { Line-1,Line-3 } as "灰色かび病+ナミハダニ";
  BOX-09: { Line-0,Line-3 } as "炭疽病+ナミハダニ";
  BOX-10: { Line-2,Line-3 } as "うどんこ病+ナミハダニ";
  BOX-11: { Line-0,Line-1,Line-2,Line-3 } as "炭疽+うどんこ+灰色かび+ナミハダニ";
  BOX-12: { Line-0,Line-1,Line-2,Line-4,Line-5 } as "炭疽+うどんこ+灰色かび+ハスモン+オオタ";
  BOX-13: { Line-1,Line-4,Line-5 } as "灰色かび+ハスモン+オオタ";
  BOX-14: { Line-2,Line-6,Line-5 } as "うどんこ+アザミウマ+オオタ";
  BOX-15: { Line-0,Line-4,Line-5 } as "炭疽+ハスモン+オオタ";
  BOX-16: { Line-0,Line-2,Line-4,Line-5,Line-6 } as "炭疽+うどんこ+ハスモン+オオタ+アザミウマ";
  BOX-17: { Line-0,Line-7 } as "炭疽病+ワタアブラムシ";
  BOX-18: { Line-1,Line-2,Line-3 } as "灰色かび+うどんこ+ナミハダニ";
  BOX-19: { Line-1,Line-2,Line-4 } as "灰色かび+うどんこ+ハスモン";
  BOX-20: { Line-1,Line-2,Line-3,Line-7 } as "灰色かび+うどんこ+ナミハダニ+ワタアブラムシ";
  BOX-21: { Line-0,Line-1,Line-2,Line-3,Line-4,Line-5 } as "炭疽+うどんこ+灰色かび+ナミハダニ+ハスモン+オオタ";
  BOX-22: { Line-0,Line-1,Line-2,Line-4,Line-5,Line-8,Line-9 } as "炭疽+うどんこ+灰色かび+ハスモン+オオタ+アブラムシ+コナジラミ";
  Bridge-Rule { match = exact;
    on-match(0) => UNDEFINED; on-match(1) => OK; on-match(>=2) => MODEL_DEFINITION_ERROR; }
  Bridge-Extension-Policy { on-UNDEFINED => auto-register; id-rule = next-max-plus-one; }
}
SpecBridge-Boundary PesticideCatalog {
  /* === 殺菌剤（病害） === */

  Candidate P01: { targets = Line-0,Line-1,Line-2;
    system="QoI"; systemCode="FRAC11"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P02: { targets = Line-0;
    system="キノキサリン系"; systemCode="FRAC_NA"; toxicityClass="劇物";
    maxApplications=2; phiDays=1; mixingBanTargets=["酸性剤","銅剤","硫黄剤"]; };

  Candidate P03: { targets = Line-0;
    system="フルアジナム系"; systemCode="FRAC7"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P04: { targets = Line-1;
    system="チアゾリジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P05: { targets = Line-0,Line-2;
    system="ジチオカーバメート系"; systemCode="FRAC_M3"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P06: { targets = Line-0,Line-2;
    system="QoI"; systemCode="FRAC11"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P07: { targets = Line-0;
    system="混合剤"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P08: { targets = Line-1,Line-2,Line-0;
    system="SDHI+QoI"; systemCode="FRAC7+FRAC11"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P09: { targets = Line-0,Line-2;
    system="QoI"; systemCode="FRAC11"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P10: { targets = Line-2;
    system="ピラゾール系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P11: { targets = Line-2;
    system="DMI（トリアゾール）"; systemCode="FRAC3"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P12: { targets = Line-1;
    system="マンジプロパミド系"; systemCode="FRAC_M14"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P13: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P14: { targets = Line-2;
    system="DMI"; systemCode="FRAC3"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P15: { targets = Line-1,Line-2;
    system="ベンズイミダゾール系"; systemCode="FRAC1"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=[]; };

  Candidate P16: { targets = Line-1;
    system="フェニルピロール"; systemCode="FRAC14"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P17: { targets = Line-1;
    system="アニリド系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P18: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P19: { targets = Line-1;
    system="ジカルボキシイミド系"; systemCode="FRAC7"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P20: { targets = Line-1;
    system="フタルイミド系"; systemCode="FRAC_M1"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P21: { targets = Line-2,Line-1;
    system="ベンズイミダゾール系"; systemCode="FRAC1"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=[]; };

  Candidate P22: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P23: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P24: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P25: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P26: { targets = Line-1;
    system="フェニルピロール"; systemCode="FRAC14"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P27: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P28: { targets = Line-2;
    system="DMI"; systemCode="FRAC3"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P29: { targets = Line-1;
    system="カルバメート系"; systemCode="FRAC_B"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  /* === 殺虫剤（害虫） === */

  Candidate P30: { targets = Line-4,Line-5;
    system="マクロライド"; systemCode="IRAC6"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P31: { targets = Line-6,Line-5;
    system="スピノシン"; systemCode="IRAC5"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P32: { targets = Line-8;
    system="ピリジン系"; systemCode="IRAC9B"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P33: { targets = Line-8;
    system="ピリジン系"; systemCode="IRAC9B"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P34: { targets = Line-4,Line-5;
    system="フェニルピロール"; systemCode="IRAC28"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P35: { targets = Line-6,Line-5;
    system="スピノシン"; systemCode="IRAC5"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P36: { targets = Line-3;
    system="METI"; systemCode="IRAC10B"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P37: { targets = Line-8;
    system="有機リン"; systemCode="IRAC1A"; toxicityClass="劇物";
    maxApplications=1; phiDays=7; mixingBanTargets=["多数"]; };

  Candidate P38: { targets = Line-3;
    system="マクロライド"; systemCode="IRAC6"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P39: { targets = Line-8,Line-9;
    system="ネオニコチノイド"; systemCode="IRAC4A"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  /* === 殺ダニ剤（害虫） === */

  Candidate P40: { targets = Line-3;
    system="METI"; systemCode="IRAC21A"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P41: { targets = Line-3;
    system="テトラジホン系"; systemCode="IRAC23"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P42: { targets = Line-3;
    system="IGR（卵阻害）"; systemCode="IRAC20"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P43: { targets = Line-3;
    system="マクロライド"; systemCode="IRAC6"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P44: { targets = Line-3;
    system="マクロライド"; systemCode="IRAC6"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P45: { targets = Line-3;
    system="テトラジホン系"; systemCode="IRAC23"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P46: { targets = Line-3;
    system="METI"; systemCode="IRAC10B"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P47: { targets = Line-4;
    system="IGR"; systemCode="IRAC7"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P48: { targets = Line-3,Line-6;
    system="物理剤"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=999; phiDays=0; mixingBanTargets=["硫黄剤"]; };

  Candidate P49: { targets = Line-3;
    system="脂質生合成阻害"; systemCode="IRAC23"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P50: { targets = Line-4;
    system="フェニルピロール"; systemCode="IRAC28"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P51: { targets = Line-1;
    system="フェニルピロール"; systemCode="FRAC14"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P52: { targets = Line-8,Line-9;
    system="ネオニコチノイド"; systemCode="IRAC4A"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P53: { targets = Line-8,Line-9;
    system="ネオニコチノイド"; systemCode="IRAC4A"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P54: { targets = Line-3;
    system="脂質生合成阻害"; systemCode="IRAC23"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P55: { targets = Line-2;
    system="無機硫黄"; systemCode="FRAC_S"; toxicityClass="普通物";
    maxApplications=999; phiDays=1; mixingBanTargets=["油剤","サフオイル"]; };

  Candidate P56: { targets = Line-3;
    system="METI"; systemCode="IRAC10B"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P57: { targets = Line-4;
    system="フェニルピロール"; systemCode="IRAC28"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P58: { targets = Line-2;
    system="無機塩"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=999; phiDays=1; mixingBanTargets=["酸性剤"]; };

  Candidate P59: { targets = Line-1;
    system="イミノクタジン系"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["展着剤過剰"]; };

  Candidate P60: { targets = Line-2;
    system="DMI（トリアゾール）"; systemCode="FRAC3"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P61: { targets = Line-0;
    system="フルアジナム系"; systemCode="FRAC7"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P62: { targets = Line-1;
    system="フタルイミド系"; systemCode="FRAC_M1"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=["アルカリ剤"]; };

  Candidate P63: { targets = Line-0;
    system="混合剤"; systemCode="FRAC_NA"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P64: { targets = Line-1;
    system="マンジプロパミド系"; systemCode="FRAC_M14"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=[]; };

  Candidate P65: { targets = Line-2,Line-1;
    system="ベンズイミダゾール系"; systemCode="FRAC1"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=[]; };

  Candidate P66: { targets = Line-1,Line-2;
    system="QoI（ストロビルリン系）"; systemCode="FRAC11"; toxicityClass="普通物";
    maxApplications=3; phiDays=1; mixingBanTargets=["銅剤"]; };

  Candidate P67: { targets = Line-0;
    system="無機銅剤"; systemCode="FRAC_M1"; toxicityClass="普通物";
    maxApplications=999; phiDays=1; mixingBanTargets=["酸性剤"]; };

  Candidate P68: { targets = Line-3;
    system="METI（複合体Ⅱ阻害）"; systemCode="IRAC21A"; toxicityClass="普通物";
    maxApplications=2; phiDays=1; mixingBanTargets=["銅剤"]; };

  SpecBridge-Rule { match-vector = EntryVector; combine = dot-product; result = TargetMatchVector; }
  Bridge-Extension-Policy { on-UNDEFINED => alert-and-halt; }
}
Reflect-Boundary SafetyChannel {
  Safety-Vector {
    usageState: "散布回数";
    intervalDays: "PHI残日数";
    rotationState: "系統連続回数";
  }

  BRIDGE L1_TARGET {
    level = 1; direction = forward;
    weight = if (targetMatch > 0) then full-pass else full-block;
    reason = "対象病害虫が要求と一致しない";
    description = "ターゲット不一致の候補を遮断";
  }

  BRIDGE L2_USAGE {
    level = 2; direction = forward;
    weight = if (usageCount >= maxApplications) then full-block else full-pass;
    reason = "散布回数上限に到達";
    description = "使用回数上限に達した候補を遮断";
  }

  BRIDGE L3_PHI {
    level = 3; direction = forward;
    weight = if (intervalDays < phiDays) then attenuate(0.5) else full-pass;
    penalty = { axis="safety", delta=-10 };
    description = "PHI残日数を満たさない候補を減衰";
  }

  BRIDGE L4_MIXING {
    level = 4; direction = forward;
    weight = if (hasMixingBan) then full-block else full-pass;
    reason = "混用禁止薬剤との組み合わせ";
    description = "混用禁止の組み合わせを遮断";
  }

  BRIDGE L5_TOXICITY {
    level = 5; direction = forward;
    weight = if (toxicityClass == "劇物") then attenuate(0.8) else full-pass;
    reason = "劇物区分のため慎重選択";
    description = "劇物薬剤を減衰（代替優先）";
  }
}

Spec-Boundary FinalSet {
  enumerate-sets {
    from = flowing;
    size = 1..2;
  }

  Set-Gate MixingSet {
    level = 5.5;
    weight = if (isPairWithInternalMixingConflict) then full-block else full-pass;
    description = "セット内候補間の混用禁止で遮断";
  }

  Mirror-ID-Rule {
    coverage = union(candidateTargetVectors);
    distance = cosine(coverage, EntryVector);
    select = max(distance);
  }

  Score-Rule {
    effectiveness = mirrorId * 10 + coverageRatio * 5;
    safety = 20 + sum-penalties("safety");
    resistance = 15 + sum-penalties("resistance");
    sort-by = [mirrorId, totalScore, set-size, id];
  }
}
