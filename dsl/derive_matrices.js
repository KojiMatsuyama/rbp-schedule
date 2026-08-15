// dsl/derive_matrices.js — 境界言語DSLのASTから
// 要求評価RBP行列（BridgeMatrix）と仕様決定RBP行列（SpecBridgeMatrix）を導出する。
// data/eval_boxes.js の EB_VECTORS/EB_MATRIX/EB_NAMES、
// rbp/spec_matching.js の TARGET_MATRIX と同じ導出方式（Object.values / map）を用いる。
const { validate } = require('./validate');
const { DslValidationError } = require('./errors');

function buildBoxVector(box, dimensions) {
  const vector = new Array(dimensions).fill(0);
  for (const idx of box.lineRefs) vector[idx] = 1;
  return vector;
}

// asName（"as \"...\""）が省略された場合の命名規則。
// rbp/eval_box_registry.js の buildEvalBoxName と同等（次元順にLine名を"+"連結）。
function buildBoxName(box, ast) {
  if (box.asName !== null) return box.asName;
  return box.lineRefs
    .slice()
    .sort((a, b) => a - b)
    .map(i => ast.domain.lines[i])
    .join('+');
}

function deriveBridgeMatrix(ast) {
  const dimensions = ast.domain.dimensions;
  const vectors = {};
  const names = {};
  for (const box of ast.bridge.boxDefs) {
    const key = `EB-${box.id}`;
    vectors[key] = buildBoxVector(box, dimensions);
    names[key] = buildBoxName(box, ast);
  }
  // EB_MATRIX = Object.values(EB_VECTORS) と同じ導出（data/eval_boxes.js）
  const matrix = Object.values(vectors);
  return { vectors, names, matrix };
}

function buildCandidateVector(cand, dimensions) {
  const vector = new Array(dimensions).fill(0);
  for (const idx of cand.targets) vector[idx] = 1;
  return vector;
}

function deriveSpecBridgeMatrix(ast) {
  const dimensions = ast.domain.dimensions;
  const candidates = ast.specBridge.candidateDefs.map(cand => {
    const record = { id: cand.id, targetVector: buildCandidateVector(cand, dimensions) };
    for (const attr of cand.attributes) record[attr.key] = attr.value;
    return record;
  });
  // TARGET_MATRIX = PESTICIDE_DB.map(p => p.targetVector) と同じ導出（rbp/spec_matching.js）
  const matrix = candidates.map(c => c.targetVector);
  return { candidates, matrix };
}

// validate()でS1〜S3・重複IDの違反を検査してから導出する。
// 違反があれば導出せずDslValidationErrorを投げる（不正なASTから行列を作らない）。
function deriveAll(ast) {
  const errors = validate(ast);
  if (errors.length > 0) {
    throw new DslValidationError(errors);
  }
  return {
    bridgeMatrix: deriveBridgeMatrix(ast),
    specBridgeMatrix: deriveSpecBridgeMatrix(ast),
  };
}

module.exports = { deriveAll, deriveBridgeMatrix, deriveSpecBridgeMatrix };
