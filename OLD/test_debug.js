const { RBP_EVAL_BOXES } = require('./RBP_EVAL_BOXES');
const { RBP_BRIDGES } = require('./RBP_BRIDGES');
const { EVAL_BOX_DATASET } = require('./21.EVAL_BOX_DATASET');

function hadamardProduct(vector, weights) {
  return vector.map((v, i) => v * weights[i]);
}

function cosineSimilarity(a, b) {
  let dot = 0, magA = 0, magB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    magA += a[i] * a[i];
    magB += b[i] * b[i];
  }
  magA = Math.sqrt(magA);
  magB = Math.sqrt(magB);
  if (magA === 0 || magB === 0) return 0;
  return dot / (magA * magB);
}

function bridgeConditionMet(originalVec, bridge) {
  const thresh = bridge.threshold_vector || bridge.bridge_vector;
  let metCount = 0;
  let totalCount = 0;

  for (let i = 0; i < thresh.length; i++) {
    if (thresh[i] === 0) continue;
    totalCount++;
    if (originalVec[i] >= thresh[i]) metCount++;
  }

  return totalCount > 0 && metCount / totalCount >= 0.7;
}

function evaluate_debug(entryVector) {
  const original = [...entryVector];
  let current = [...entryVector];
  let currentLineId = "LINE-EB02";

  const levels = {};
  for (const b of RBP_BRIDGES) {
    if (!levels[b.level]) levels[b.level] = [];
    levels[b.level].push(b);
  }

  const sortedLevels = Object.keys(levels).map(Number).sort((a, b) => a - b);

  console.log('=== 評価フロー開始 ===');
  console.log('初期LINE:', currentLineId);

  for (const level of sortedLevels) {
    console.log(`\nLevel ${level} 候補:`);
    const candidates = levels[level];
    let applied = false;

    for (const bridge of candidates) {
      console.log(`  チェック: ${bridge.id}`);
      if (applied) {
        console.log(`    → スキップ（既に適用済み）`);
        break;
      }

      if (bridge.from_line !== currentLineId) {
        console.log(`    → from_line=${bridge.from_line} 不一致（currentLine=${currentLineId}）`);
        continue;
      }

      console.log(`    → from_line一致。条件判定...`);
      if (bridgeConditionMet(original, bridge)) {
        console.log(`    ✓ 条件満たす！適用中...`);
        const weights = bridge.weight_vector || bridge.bridge_vector;
        current = hadamardProduct(current, weights);
        currentLineId = bridge.to_line;
        applied = true;
        console.log(`    → 新LINE: ${currentLineId}`);
      } else {
        console.log(`    ✗ 条件未満`);
      }
    }
  }

  let bestBox = null;
  let bestScore = -1;

  for (const box of RBP_EVAL_BOXES) {
    const score = cosineSimilarity(current, box.eval_vector);
    if (score > bestScore) {
      bestScore = score;
      bestBox = box;
    }
  }

  return {
    final_line: currentLineId,
    evaluated_box: bestBox,
    confidence: bestScore
  };
}

// テスト
const ev008 = EVAL_BOX_DATASET.find(e => e.ENTRY_ID === 'EV-008');
console.log('入力ベクトル:', ev008.vector);
const result = evaluate_debug(ev008.vector);
console.log('\n=== 結果 ===');
console.log('最終LINE:', result.final_line);
console.log('最終BOX:', result.evaluated_box.id);
