// dsl/validate.js — 境界言語BNF.txt §8 の静的意味規則を検査する。
// (S1) dimensionsとDemand-BoundaryのLine-Def数が一致するか
// (S2) BOX-DefのLine参照が[0,dimensions)内か
// (S3) Candidate-Defのtargets参照が[0,dimensions)内か
// (+) BOX-Id / Candidate-Id の重複定義
// 1件見つかった時点で止めず、全違反をまとめて返す（コンパイル時の一括診断のため）。
const { DslError } = require('./errors');

function validate(ast) {
  const errors = [];
  const dimensions = ast.domain.dimensions;

  if (ast.demand.lineDefs.length !== dimensions) {
    errors.push(new DslError(
      'S1_DIMENSION_MISMATCH',
      `Domain ${ast.domain.id} は dimensions=${dimensions} だが Demand-Boundary ${ast.demand.id} の Line-Def は${ast.demand.lineDefs.length}件（${dimensions}件必要）`,
      ast.demand.lineDefs.length > 0 ? ast.demand.lineDefs[0].line : 0
    ));
  }

  const boxIds = new Map();
  for (const box of ast.bridge.boxDefs) {
    if (boxIds.has(box.id)) {
      errors.push(new DslError(
        'DUPLICATE_BOX_ID',
        `BOX-${box.id} は Bridge-Boundary ${ast.bridge.id} 内で複数回定義されています（最初の定義: line ${boxIds.get(box.id)}）`,
        box.line
      ));
    } else {
      boxIds.set(box.id, box.line);
    }
    for (const ref of box._rawRefs) {
      if (ref.index < 0 || ref.index >= dimensions) {
        errors.push(new DslError(
          'S2_LINE_REF_OUT_OF_RANGE',
          `BOX-${box.id} が参照する Line-${ref.index} は Domain ${ast.domain.id} の範囲 [0,${dimensions}) 外です`,
          ref.line
        ));
      }
    }
  }

  const candidateIds = new Map();
  for (const cand of ast.specBridge.candidateDefs) {
    if (candidateIds.has(cand.id)) {
      errors.push(new DslError(
        'DUPLICATE_CANDIDATE_ID',
        `Candidate ${cand.id} は SpecBridge-Boundary ${ast.specBridge.id} 内で複数回定義されています（最初の定義: line ${candidateIds.get(cand.id)}）`,
        cand.line
      ));
    } else {
      candidateIds.set(cand.id, cand.line);
    }
    for (const ref of cand._rawTargets) {
      if (ref.index < 0 || ref.index >= dimensions) {
        errors.push(new DslError(
          'S3_TARGET_REF_OUT_OF_RANGE',
          `Candidate ${cand.id} が参照する Line-${ref.index} は Domain ${ast.domain.id} の範囲 [0,${dimensions}) 外です`,
          ref.line
        ));
      }
    }
  }

  return errors;
}

module.exports = { validate };
