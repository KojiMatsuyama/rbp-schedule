# EVAL_BOX Definition — BNF準拠
# 生成規則: BOX-<BOX-Id>: { <Line-Ref-Set> } [ "as" <String> ];
# Line番号はBNF通り1-indexed。ベクトルはパーサーが自動計算する。

Domain STB {
  dimensions: 10;
  lines: ["炭疽病","灰色かび病","うどんこ病","ナミハダニ","ハスモンヨトウ",
          "オオタバコガ","ミカンキイロアザミウマ","ワタアブラムシ",
          "アブラムシ","コナジラミ"];
}

Demand-Boundary PestDemand {
  Line-1: 炭疽病(disease);
  Line-2: 灰色かび病(disease);
  Line-3: うどんこ病(disease);
  Line-4: ナミハダニ(pest);
  Line-5: ハスモンヨトウ(pest);
  Line-6: オオタバコガ(pest);
  Line-7: ミカンキイロアザミウマ(pest);
  Line-8: ワタアブラムシ(pest);
  Line-9: アブラムシ(pest);
  Line-10: コナジラミ(pest);
  Demand-Rule { on-occurrence => Line-i = 1; on-absence => Line-i = 0; }
}

Bridge-Boundary EvalBox {
  BOX-01: { Line-1 } as "炭疽病";
  BOX-02: { Line-2 } as "灰色かび病";
  BOX-03: { Line-3 } as "うどんこ病";
  BOX-04: { Line-1, Line-3 } as "炭疽+うどんこ";
  BOX-05: { Line-1, Line-2 } as "炭疽+灰色かび";
  BOX-06: { Line-2, Line-3 } as "灰色かび+うどんこ";
  BOX-07: { Line-1, Line-2, Line-3 } as "炭疽+灰色かび+うどんこ";
  BOX-08: { Line-2, Line-4 } as "灰色かび+ナミハダニ";
  BOX-09: { Line-1, Line-4 } as "炭疽+ナミハダニ";
  BOX-10: { Line-3, Line-4 } as "うどんこ+ナミハダニ";
  BOX-11: { Line-1, Line-2, Line-3, Line-4 } as "炭疽+灰色かび+うどんこ+ナミハダニ";
  BOX-12: { Line-1, Line-2, Line-3, Line-5, Line-6 } as "炭疽+灰色かび+うどんこ+ハスモン+オオタ";
  BOX-13: { Line-2, Line-5, Line-6 } as "灰色かび+ハスモン+オオタ";
  BOX-14: { Line-3, Line-6, Line-7 } as "うどんこ+オオタ+ミカン";
  BOX-15: { Line-1, Line-5, Line-6 } as "炭疽+ハスモン+オオタ";
  BOX-16: { Line-1, Line-3, Line-5, Line-6, Line-7 } as "炭疽+うどんこ+ハスモン+オオタ+ミカン";
  BOX-17: { Line-1, Line-8 } as "炭疽+ワタアブラムシ";
  BOX-18: { Line-2, Line-3, Line-4 } as "灰色かび+うどんこ+ナミハダニ";
  BOX-19: { Line-2, Line-3, Line-5 } as "灰色かび+うどんこ+ハスモン";
  BOX-20: { Line-2, Line-3, Line-4, Line-8 } as "灰色かび+うどんこ+ナミ+ワタアブラムシ";
  BOX-21: { Line-1, Line-2, Line-3, Line-4, Line-5, Line-6 } as "炭疽+灰色かび+うどんこ+ナミ+ハスモン+オオタ";
  BOX-22: { Line-1, Line-2, Line-3, Line-5, Line-6, Line-9, Line-10 } as "炭疽+灰色かび+うどんこ+ハスモン+オオタ+アブラムシ+コナジラミ";

  Bridge-Rule {
    match = exact;
    on-match(0) => UNDEFINED;
    on-match(1) => OK;
    on-match(>=2) => MODEL_DEFINITION_ERROR;
  }

  Bridge-Extension-Policy {
    on-UNDEFINED => auto-register;
    id-rule = next-max-plus-one;
    naming-rule = "{diseases joined by '+'}";
  }
}
