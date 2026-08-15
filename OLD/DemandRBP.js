// DemandRBP — 要求評価RBPパイプライン構造（マスター）
// ENTRY_VECTOR → BRIDGE（条件付き重み適用） → LINE（縦パイプ） → EVAL_BOX（評価）
//
// 意味流（Meaning Flow）モデル：
//   ENTRY_VECTOR は「水の初期状態」
//   BRIDGE は threshold_vector（条件判定）と weight_vector（変換重み）の2つを持つ
//   LINE は「評価BOXへ向かう縦パイプ」
//   EVAL_BOX は「意味空間の基準点」
//
// ★ 新方式: EB評価ベクトル行列 M（22×10）による直接スコアリング
//   入力ベクトル v に対して、M × v^T の行列演算で全EBスコアを一度に計算。
//
// 数学的形式:
//   CosineSim: s = M̂ × v̂^T    (M̂ = NormalizeRows(M), v̂ = Normalize(v))
//   DotProd:   s = M × v^T
//   Bridge:    E' = W_diag × E  (W_diag = diag(w))

const { RBP_EVAL_BOXES } = require('./RBP_EVAL_BOXES');
const { RBP_LINES } = require('./RBP_LINES');
const { RBP_BRIDGES } = require('./RBP_BRIDGES');
const { EVAL_BOX_DATASET } = require('./21.EVAL_BOX_DATASET');

// ── 行列演算エンジン ──
const {
  Matrix,
  dotProduct,
  norm,
  normalize,
  cosineSimilarity,
  hadamardProduct,
  cosineSimMatrix,
  dotProductMatrix,
  scoreMatrixMax,
  scoreDotProductMax,
  bridgeTransform,
  printMatrix,
  printScores
} = require('./MatrixEngine');

// ── EB評価ベクトル行列 M（22×10） ──
// 各EBのeval_vectorを行列としたもの。
const EB_MATRIX_DATA = RBP_EVAL_BOXES.map(box => box.eval_vector);
const EB_MATRIX = new Matrix(EB_MATRIX_DATA);

// ── 意味流エンジン ──

/**
 * Hadamard積（要素ごとの積）— 互換性用
 */
function hadamardProductLocal(vector, weights) {
  return hadamardProduct(vector, weights);
}

/**
 * コサイン類似度 — 互換性用
 */
function cosineSimilarityLocal(a, b) {
  return cosineSimilarity(a, b);
}

/**
 * ベクトルの内積 — 互換性用
 */
function dotProductLocal(a, b) {
  return dotProduct(a, b);
}

/**
 * BRIDGEの条件判定
 */
function bridgeConditionMet(originalVec, bridge) {
  const thresh = bridge.threshold_vector || bridge.bridge_vector;
  let metCount = 0;
  let totalCount = 0;

  for (let i = 0; i < thresh.length; i++) {
    if (thresh[i] === 0) continue;
    totalCount++;
    const isMet = thresh[i] > 0
      ? originalVec[i] >= thresh[i]
      : originalVec[i] <= -thresh[i];
    if (isMet) metCount++;
  }

  return totalCount > 0 && metCount / totalCount >= 0.7;
}

// ═══════════════════════════════════════════════════════════
// 【行列演算方式】EB評価 — ループを排除し明示的な行列積に
// ═══════════════════════════════════════════════════════════

/**
 * 【行列演算】EB評価ベクトル行列 M による直接スコアリング（コサイン類似度）
 *
 * 数学式:
 *   v̂ = v / ||v||₂                              — 入力の正規化
 *   M̂ = NormalizeRows(M)                         — 各行の正規化
 *   s = M̂ × v̂^T                                 — 行列×ベクトル（全EBスコア同時計算）
 *   i* = argmaxᵢ sᵢ                              — 最大スコアのEBを選択
 *
 * 従来のループ版との比較:
 *   旧: for i=0..21: s[i] = cos(M[i], v)   (22回の個別計算)
 *   新: s = M̂ × v̂^T                         (1回の行列積)
 *
 * @param {number[]} entryVector - 10次元の入力ベクトル
 * @returns {{box: Object, score: number, allScores: number[], matrixOp: string}}
 */
function scoreAgainstMatrix(entryVector) {
  // Step 1: 行列 × ベクトルの転置 → 全スコアベクトル
  const { scores, boxIndices } = cosineSimMatrix(EB_MATRIX, entryVector);

  // Step 2: スコアベクトルから最大値のインデックスを取得
  let bestIdx = 0;
  let bestScore = -Infinity;
  for (let i = 0; i < scores.length; i++) {
    if (scores[i] > bestScore) {
      bestScore = scores[i];
      bestIdx = i;
    }
  }

  // Step 3: 結果を構造化
  const allScores = {};
  for (let i = 0; i < boxIndices.length; i++) {
    allScores[RBP_EVAL_BOXES[boxIndices[i]].id] = scores[i];
  }

  return {
    box: RBP_EVAL_BOXES[bestIdx],
    score: bestScore,
    allScores,
    _matrixOperation: `M̂(${EB_MATRIX.rows}×${EB_MATRIX.cols}) × v̂^T(${entryVector.length}×1) → s(${EB_MATRIX.rows}×1)`,
    _intermediate: {
      vNorm: norm(entryVector),
      MHatNorm: EB_MATRIX.normalizeRows().frobeniusNorm(),
      scoresVector: scores
    }
  };
}

/**
 * 【行列演算】EB評価ベクトル行列 M による直接スコアリング（内積）
 *
 * 数学式:
 *   s = M × v^T                                — 行列×ベクトル（全EBスコア同時計算）
 *   i* = argmaxᵢ sᵢ                            — 最大スコアのEBを選択
 *
 * 従来のループ版との比較:
 *   旧: for i=0..21: s[i] = M[i]・v           (22回の個別内積)
 *   新: s = M × v^T                            (1回の行列積)
 *
 * @param {number[]} entryVector - 10次元の入力ベクトル
 * @returns {{box: Object, score: number, allScores: number[], matrixOp: string}}
 */
function scoreByDotProduct(entryVector) {
  // Step 1: 行列 × ベクトルの転置 → 全スコアベクトル
  const { scores, boxIndices } = dotProductMatrix(EB_MATRIX, entryVector);

  // Step 2: スコアベクトルから最大値のインデックスを取得
  let bestIdx = 0;
  let bestScore = -Infinity;
  for (let i = 0; i < scores.length; i++) {
    if (scores[i] > bestScore) {
      bestScore = scores[i];
      bestIdx = i;
    }
  }

  // Step 3: 結果を構造化
  const allScores = {};
  for (let i = 0; i < boxIndices.length; i++) {
    allScores[RBP_EVAL_BOXES[boxIndices[i]].id] = scores[i];
  }

  return {
    box: RBP_EVAL_BOXES[bestIdx],
    score: bestScore,
    allScores,
    _matrixOperation: `M(${EB_MATRIX.rows}×${EB_MATRIX.cols}) × v^T(${entryVector.length}×1) → s(${EB_MATRIX.rows}×1)`,
    _intermediate: {
      scoresVector: scores
    }
  };
}

// ═══════════════════════════════════════════════════════════
// 【従来方式】BRIDGE経由のパイプライン評価（変更なし）
// ═══════════════════════════════════════════════════════════

/**
 * 【従来方式】BRIDGE経由のエンドツーエンド評価
 */
function evaluatePipeline(entryVector, bridges = RBP_BRIDGES) {
  const original = [...entryVector];
  let current = [...entryVector];
  let currentLineId = "LINE-EB02";

  const levels = {};
  for (const b of bridges) {
    if (!levels[b.level]) levels[b.level] = [];
    levels[b.level].push(b);
  }

  const sortedLevels = Object.keys(levels).map(Number).sort((a, b) => a - b);

  for (const level of sortedLevels) {
    const candidates = levels[level];
    let applied = false;

    for (const bridge of candidates) {
      if (applied) break;
      if (bridge.from_line !== currentLineId) continue;

      if (bridgeConditionMet(original, bridge)) {
        const weights = bridge.weight_vector || bridge.bridge_vector;
        current = hadamardProductLocal(current, weights);
        currentLineId = bridge.to_line;
        applied = true;
      }
    }
  }

  // ── 最終EB選択: 行列演算（Method B: Broadcast-Hadamard-Reduce） ──
  // 従来: for each box: cosineSimilarity(current, box.eval_vector)
  // 新:   M_final × v̂^T → 全スコアを1回の行列積で計算
  const M_final_data = RBP_EVAL_BOXES.map(box => box.eval_vector);
  const M_final = new Matrix(M_final_data);
  const { scores: pipelineScores } = cosineSimMatrix(M_final, current);

  let bestBox = null;
  let bestScore = -1;
  for (let i = 0; i < pipelineScores.length; i++) {
    if (pipelineScores[i] > bestScore) {
      bestScore = pipelineScores[i];
      bestBox = RBP_EVAL_BOXES[i];
    }
  }

  return {
    input_vector: entryVector,
    final_vector: current,
    evaluated_box: bestBox,
    confidence: bestScore,
    final_line: currentLineId
  };
}

// ═══════════════════════════════════════════════════════════
// 【行列演算】BRIDGE変換 — 対角行列による表現
// ═══════════════════════════════════════════════════════════

/**
 * 【行列演算】BRIDGE条件判定 + 重み適用（行列形式）
 *
 * 数学式:
 *   条件: (original ⊙ |t|) ・ sign(t) ≥ τ × ||t||₁
 *   変換: E' = Diag(w) × E   （対角行列による左乗算）
 *
 * @param {number[]} entryVector - 入力ベクトル
 * @param {Object} bridge - BRIDGE定義
 * @param {Matrix} E - 入力行列（複数サンプルがある場合）
 * @returns {{met: boolean, transformed: Matrix|string}}
 */
function evaluateBridgeMatrix(entryVector, bridge, E) {
  const thresh = bridge.threshold_vector || bridge.bridge_vector;
  const weights = bridge.weight_vector || bridge.bridge_vector;

  // 条件判定
  const met = bridgeConditionMet(entryVector, bridge);

  if (met && E) {
    // 対角行列による変換: E' = Diag(w) × E
    const transformed = bridgeTransform(new Matrix(E.data), weights);
    return { met: true, transformed };
  }

  return { met, transformed: met ? hadamardProductLocal(entryVector, weights) : entryVector };
}

// ═══════════════════════════════════════════════════════════
// DemandRBP構造体（DSL準拠）
// ═══════════════════════════════════════════════════════════

const DemandRBP = {
  entries: EVAL_BOX_DATASET.map(e => ({
    id: e.ENTRY_ID,
    date: e.date,
    entry_vector: e.vector
  })),

  lines: RBP_LINES,
  bridges: RBP_BRIDGES,
  eval_boxes: RBP_EVAL_BOXES,

  // 行列オブジェクト
  EB_MATRIX,
  EB_MATRIX_DATA,

  /**
   * 新方式: 行列 M による直接スコアリングで全エントリ評価
   * 内部で cosineSimMatrix(M, v) を使用（行列×ベクトル）
   */
  evaluateAllMatrix() {
    return EVAL_BOX_DATASET.map(entry => {
      const result = scoreAgainstMatrix(entry.vector);
      return {
        ENTRY_ID: entry.ENTRY_ID,
        date: entry.date,
        entry_vector: entry.vector,
        predicted_box: result.box.id,
        predicted_name: result.box.name,
        confidence: result.score,
        actual_box: entry.best_eb.label,
        actual_name: entry.best_eb.name,
        match: result.box.id === entry.best_eb.label,
        all_scores: result.allScores,
        matrix_operation: result._matrixOperation
      };
    });
  },

  /**
   * 旧方式: BRIDGEパイプライン評価
   */
  evaluateAll() {
    return EVAL_BOX_DATASET.map(entry => {
      const result = evaluatePipeline(entry.vector);
      return {
        ENTRY_ID: entry.ENTRY_ID,
        date: entry.date,
        entry_vector: entry.vector,
        predicted_box: result.evaluated_box.id,
        predicted_name: result.evaluated_box.name,
        confidence: result.confidence,
        actual_box: entry.best_eb.label,
        actual_name: entry.best_eb.name,
        match: result.evaluated_box.id === entry.best_eb.label
      };
    });
  },

  accuracyReport() {
    const results = this.evaluateAll();
    const total = results.length;
    const correct = results.filter(r => r.match).length;
    const byBox = {};
    for (const r of results) {
      const key = `${r.predicted_box}/${r.actual_box}`;
      byBox[key] = (byBox[key] || 0) + 1;
    }
    return {
      total,
      correct,
      accuracy: (correct / total * 100).toFixed(1) + '%',
      confusion_matrix: byBox
    };
  },

  accuracyMatrixReport() {
    const results = this.evaluateAllMatrix();
    const total = results.length;
    const correct = results.filter(r => r.match).length;
    const byBox = {};
    for (const r of results) {
      const key = `${r.predicted_box}/${r.actual_box}`;
      byBox[key] = (byBox[key] || 0) + 1;
    }
    return {
      total,
      correct,
      accuracy: (correct / total * 100).toFixed(1) + '%',
      confusion_matrix: byBox
    };
  },

  errorReport() {
    return this.evaluateAll().filter(r => !r.match);
  },

  errorMatrixReport() {
    return this.evaluateAllMatrix().filter(r => !r.match);
  },

  /**
   * 内積方式: 行列 M × v^T で全エントリ評価
   */
  evaluateAllDotProduct() {
    return EVAL_BOX_DATASET.map(entry => {
      const result = scoreByDotProduct(entry.vector);
      return {
        ENTRY_ID: entry.ENTRY_ID,
        date: entry.date,
        entry_vector: entry.vector,
        predicted_box: result.box.id,
        predicted_name: result.box.name,
        confidence: result.score,
        actual_box: entry.best_eb.label,
        actual_name: entry.best_eb.name,
        match: result.box.id === entry.best_eb.label,
        all_scores: result.allScores,
        matrix_operation: result._matrixOperation
      };
    });
  },

  /**
   * 内積方式の精度レポート
   */
  accuracyDotProductReport() {
    const results = this.evaluateAllDotProduct();
    const total = results.length;
    const correct = results.filter(r => r.match).length;
    const byBox = {};
    for (const r of results) {
      const key = `${r.predicted_box}/${r.actual_box}`;
      byBox[key] = (byBox[key] || 0) + 1;
    }
    return {
      total,
      correct,
      accuracy: (correct / total * 100).toFixed(1) + '%',
      confusion_matrix: byBox
    };
  },

  /**
   * 内積方式のエラー詳細
   */
  errorDotProductReport() {
    return this.evaluateAllDotProduct().filter(r => !r.match);
  }
};

module.exports = {
  DemandRBP,
  evaluate: evaluatePipeline,
  scoreAgainstMatrix,
  scoreByDotProduct,
  scoreMatrixMax,
  scoreDotProductMax,
  hadamardProduct: hadamardProductLocal,
  bridgeConditionMet,
  // 行列演算エンジンもエクスポート
  Matrix,
  cosineSimMatrix,
  dotProductMatrix,
  bridgeTransform,
  norm,
  normalize,
  cosineSimilarity,
  dotProduct,
  hadamardProduct,
  // ヘルパー
  printMatrix,
  printScores
};
