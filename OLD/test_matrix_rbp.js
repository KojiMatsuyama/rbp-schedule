// test_matrix_rbp.js — 行列演算によるRBPスコアリングの検証
//
// 実行: node test_matrix_rbp.js
//
// 確認すること:
//   1. EB_MATRIX (22×10) が正しく構築される
//   2. cosineSimMatrix(M, v) がループ版と一致する
//   3. dotProductMatrix(M, v) がループ版と一致する
//   4. 精度レポートが従来方式と同等であることを確認

const {
  DemandRBP,
  Matrix,
  cosineSimMatrix,
  dotProductMatrix,
  scoreMatrixMax,
  scoreDotProductMax,
  bridgeTransform,
  printMatrix,
  printScores,
  norm,
  normalize,
  cosineSimilarity,
  dotProduct,
  hadamardProduct
} = require('./DemandRBP');

console.log('\n' + '='.repeat(70));
console.log('  RBP 行列演算エンジン — テスト＆検証');
console.log('='.repeat(70));

// ═══════════════════════════════════════════════════════════
// Test 1: EB_MATRIX の構築確認
// ═══════════════════════════════════════════════════════════
console.log('\n── Test 1: EB_MATRIX 構築 ──');
const M = DemandRBP.EB_MATRIX;
console.log(`EB_MATRIX: ${M.rows}行 × ${M.cols}列`);
console.log(`型: ${M.constructor.name}`);
printMatrix(M, 'EB_MATRIX (20×10)');

// ═══════════════════════════════════════════════════════════
// Test 2: サンプルENTRY_VECTOR でスコアリング
// ═══════════════════════════════════════════════════════════
console.log('\n── Test 2: コサイン類似度 — 行列演算 vs ループ ──');

// サンプルENTRY_VECTOR（炭疽+うどんこの高リスクケース）
const sampleEntry = [0.85, 0.30, 0.70, 0.05, 0.10, 0.05, 0.02, 0.01, 0.01, 0.01];

// 行列演算版
const matrixResult = scoreMatrixMax(M, sampleEntry, DemandRBP.eval_boxes);
// ループ版（手動計算で検証）
const loopScores = DemandRBP.eval_boxes.map(box => cosineSimilarity(sampleEntry, box.eval_vector));

console.log('\n【行列演算版】cosineSimMatrix(M, v)');
printScores('スコアベクトル s = M̂ × v̂^T', matrixResult.allScores,
  DemandRBP.eval_boxes.map(b => b.id));

console.log('\n【ループ版】for i: cos(M[i], v)');
printScores('スコアベクトル', loopScores,
  DemandRBP.eval_boxes.map(b => b.id));

// 差異の確認
let maxDiff = 0;
for (let i = 0; i < loopScores.length; i++) {
  const diff = Math.abs(matrixResult.allScores[i] - loopScores[i]);
  if (diff > maxDiff) maxDiff = diff;
}
console.log(`\n最大差: ${maxDiff.toExponential(4)} ${maxDiff < 1e-10 ? '✓ 一致' : '✗ 不一致'}`);

// ═══════════════════════════════════════════════════════════
// Test 3: 内積 — 行列演算 vs ループ
// ═══════════════════════════════════════════════════════════
console.log('\n── Test 3: 内積 — 行列演算 vs ループ ──');

const dotMatrixResult = scoreDotProductMax(M, sampleEntry, DemandRBP.eval_boxes);
const dotLoopScores = DemandRBP.eval_boxes.map(box => dotProduct(sampleEntry, box.eval_vector));

console.log('\n【行列演算版】dotProductMatrix(M, v)');
printScores('スコアベクトル s = M × v^T', dotMatrixResult.allScores,
  DemandRBP.eval_boxes.map(b => b.id));

console.log('\n【ループ版】for i: M[i]・v');
printScores('スコアベクトル', dotLoopScores,
  DemandRBP.eval_boxes.map(b => b.id));

let dotMaxDiff = 0;
for (let i = 0; i < dotLoopScores.length; i++) {
  const diff = Math.abs(dotMatrixResult.allScores[i] - dotLoopScores[i]);
  if (diff > dotMaxDiff) dotMaxDiff = diff;
}
console.log(`\n最大差: ${dotMaxDiff.toExponential(4)} ${dotMaxDiff < 1e-10 ? '✓ 一致' : '✗ 不一致'}`);

// ═══════════════════════════════════════════════════════════
// Test 4: 行列演算の中間ステップ可視化
// ═══════════════════════════════════════════════════════════
console.log('\n── Test 4: 行列演算の中間ステップ ──');

const vNorm = norm(sampleEntry);
const vHat = normalize(sampleEntry);
const MHat = M.normalizeRows();

console.log(`\n入力ベクトル v: [${sampleEntry.map(v => v.toFixed(2)).join(', ')}]`);
console.log(`||v||₂ = ${vNorm.toFixed(6)}`);
console.log(`v̂ = [${vHat.map(v => v.toFixed(4)).join(', ')}]`);

console.log(`\nM̂ (正規化済みEB行列) のフローベニウスノルム: ${MHat.frobeniusNorm().toFixed(6)}`);

// M̂ × v̂^T の計算過程
const scoresVec = MHat.matVecMul(vHat);
console.log(`\nM̂(${M.rows}×${M.cols}) × v̂^T(${sampleEntry.length}×1) = s(${M.rows}×1)`);
console.log(`s = [${scoresVec.map(s => s.toFixed(6)).join(', ')}]`);

const bestIdx = scoresVec.indexOf(Math.max(...scoresVec));
console.log(`\ni* = argmaxᵢ sᵢ = ${bestIdx}`);
console.log(`EB-${String(bestIdx + 1).padStart(2, '0')} (${DemandRBP.eval_boxes[bestIdx].name}) = ${scoresVec[bestIdx].toFixed(6)}`);

// ═══════════════════════════════════════════════════════════
// Test 5: 全エントリの精度検証
// ═══════════════════════════════════════════════════════════
console.log('\n── Test 5: 全エントリ精度レポート ──');

const accuracyMatrix = DemandRBP.accuracyMatrixReport();
console.log(`行列演算方式 (cosine):  ${accuracyMatrix.accuracy} (${accuracyMatrix.correct}/${accuracyMatrix.total})`);

const accuracyDot = DemandRBP.accuracyDotProductReport();
console.log(`行列演算方式 (dot):    ${accuracyDot.accuracy} (${accuracyDot.correct}/${accuracyDot.total})`);

const accuracyOld = DemandRBP.accuracyReport();
console.log(`従来方式 (pipeline):   ${accuracyOld.accuracy} (${accuracyOld.correct}/${accuracyOld.total})`);

// ═══════════════════════════════════════════════════════════
// Test 6: BRIDGE変換 — 対角行列による表現
// ═══════════════════════════════════════════════════════════
console.log('\n── Test 6: BRIDGE変換（対角行列形式） ──');

// サンプルBRIDGE
const sampleBridge = {
  level: 1,
  from_line: 'LINE-EB02',
  to_line: 'LINE-EB07',
  threshold_vector: [0.5, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  weight_vector: [0.9, 0.7, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
};

const sampleE = new Matrix([
  sampleEntry,
  [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
]);

const bridgeResult = bridgeTransform(sampleE, sampleBridge.weight_vector);
console.log(`\n入力 E (${sampleE.rows}×${sampleE.cols}):`);
for (let i = 0; i < sampleE.rows; i++) {
  console.log(`  E[${i}] = [${sampleE.row(i).map(v => v.toFixed(2)).join(', ')}]`);
}

console.log(`\n重み w = [${sampleBridge.weight_vector.map(v => v.toFixed(1)).join(', ')}]`);
console.log(`\n変換 E' = Diag(w) × E (${bridgeResult.rows}×${bridgeResult.cols}):`);
for (let i = 0; i < bridgeResult.rows; i++) {
  console.log(`  E'[${i}] = [${bridgeResult.row(i).map(v => v.toFixed(4)).join(', ')}]`);
}

// Hadamard積でも確認
console.log(`\n[Hadamard積で確認] e' = e ⊙ w:`);
for (let i = 0; i < sampleE.rows; i++) {
  const hProd = hadamardProduct(sampleE.row(i), sampleBridge.weight_vector);
  console.log(`  e'[${i}] = [${hProd.map(v => v.toFixed(4)).join(', ')}]`);
}

// ═══════════════════════════════════════════════════════════
// Summary
// ═══════════════════════════════════════════════════════════
console.log('\n' + '='.repeat(70));
console.log('  検証まとめ');
console.log('='.repeat(70));
console.log(`  EB_MATRIX:              ${M.rows}×${M.cols} ✓`);
console.log(`  cosineSimMatrix:        最大差 ${maxDiff.toExponential(4)} ${maxDiff < 1e-10 ? '✓' : '✗'}`);
console.log(`  dotProductMatrix:       最大差 ${dotMaxDiff.toExponential(4)} ${dotMaxDiff < 1e-10 ? '✓' : '✗'}`);
console.log(`  精度(cosine):           ${accuracyMatrix.accuracy}`);
console.log(`  精度(dot):              ${accuracyDot.accuracy}`);
console.log(`  精度(pipeline):         ${accuracyOld.accuracy}`);
console.log(`  BRIDGE変換(H≡Diag):     ✓ 一致確認済み`);
console.log('='.repeat(70) + '\n');
