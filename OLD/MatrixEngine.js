// MatrixEngine — 行列演算エンジン（外部依存なし・研究用ミニマル実装）
//
// RBPの行列演算を明示的に表現するための最小限の行列クラス。
// 内部表現は1次元フラット配列（row-major）。
//
// 数学的操作:
//   MatMul(A, x)     → 行列 × ベクトル  (Ax)
//   CosineSimMatrix(M, v) → 全行とのコサイン類似度ベクトル
//   DotProductMatrix(M, v) → 全行との内積ベクトル
//   Hadamard(A, B)   → 要素ごとの積
//   Norm(x)          → ユークリッドノルム
//   Normalize(x)     → 正規化ベクトル

// ═══════════════════════════════════════════════════════════
// 基本型
// ═══════════════════════════════════════════════════════════

/**
 * Matrix — 2次元行列（row-majorフラット配列）
 * @prop {number[][]} data   - 二次元配列（人間 readable）
 * @prop {number} rows       - 行数
 * @prop {number} cols       - 列数
 */
class Matrix {
  /**
   * @param {number[][]} data - 二次元配列 [[row0], [row1], ...]
   */
  constructor(data) {
    this.data = data;
    this.rows = data.length;
    this.cols = (data[0] ? data[0].length : 0);
  }

  /**
   * インデックス [i][j] アクセス
   */
  get(i, j) {
    return this.data[i][j];
  }

  /**
   * 行 i を返す（ベクトルとして）
   */
  row(i) {
    return this.data[i];
  }

  /**
   * 列 j を返す
   */
  col(j) {
    return this.data.map(r => r[j]);
  }

  /**
   * 行列を二次元配列に変換
   */
  toJSON() {
    return this.data;
  }

  /**
   * 行列の転置 M^T
   */
  transpose() {
    const t = Array.from({ length: this.cols }, (_, j) =>
      Array.from({ length: this.rows }, (_, i) => this.get(i, j))
    );
    return new Matrix(t);
  }

  /**
   * 行列のトレース Tr(M) — 正方行列限定
   */
  trace() {
    if (this.rows !== this.cols) throw new Error('Not square');
    let s = 0;
    for (let i = 0; i < this.rows; i++) s += this.get(i, i);
    return s;
  }

  /**
   * 行列のフローベニウスノルム ||M||_F = sqrt(sum of squares)
   */
  frobeniusNorm() {
    let s = 0;
    for (let i = 0; i < this.rows; i++)
      for (let j = 0; j < this.cols; j++)
        s += this.get(i, j) ** 2;
    return Math.sqrt(s);
  }

  /**
   * 行列 × 行列
   * @param {Matrix} B - 右側の行列 (colsA == rowsB)
   * @returns {Matrix} A × B
   */
  matMul(B) {
    if (this.cols !== B.rows)
      throw new Error(`Dimension mismatch: ${this.cols} != ${B.rows}`);
    const BT = B.transpose();
    const result = Array.from({ length: this.rows }, (_, i) =>
      BT.data.map(bRow => dotProduct(this.row(i), bRow))
    );
    return new Matrix(result);
  }

  /**
   * 行列 × ベクトル  Ax
   * @param {number[]} x - 列ベクトル
   * @returns {number[]} 結果ベクトル
   */
  matVecMul(x) {
    if (this.cols !== x.length)
      throw new Error(`Dimension mismatch: ${this.cols} != ${x.length}`);
    return Array.from({ length: this.rows }, (_, i) =>
      dotProduct(this.row(i), x)
    );
  }

  /**
   * 行列 × 列ベクトル → 結果を列ベクトルとして Matrix に包んで返す
   * M × x → Matrix(n×1)
   * @param {number[]} x
   * @returns {Matrix}
   */
  multiplyColumn(x) {
    return new Matrix(this.matVecMul(x).map(v => [v]));
  }

  /**
   * 行列 × 転置された列ベクトル
   * M × x^T → Matrix(n×1)
   * 数学表記: M x^T
   * @param {number[]} x
   * @returns {Matrix}
   */
  multiplyTranspose(x) {
    return this.multiplyColumn(x);
  }

  /**
   * 行列の各行のユークリッドノルムをベクトルとして返す
   * ||M[i]||₂ for each row i
   * @returns {number[]}
   */
  rowNorms() {
    return this.data.map(row => norm(row));
  }

  /**
   * 行列の各行を正規化した行列を返す
   * M_norm[i] = M[i] / ||M[i]||₂
   * @returns {Matrix}
   */
  normalizeRows() {
    const norms = this.rowNorms();
    const normalized = this.data.map((row, i) =>
      norms[i] === 0 ? row.map(() => 0) : row.map(v => v / norms[i])
    );
    return new Matrix(normalized);
  }

  /**
   * 行列 × 行列（標準的な三重ループ）
   * @param {Matrix} B
   * @returns {Matrix}
   */
  matMulFull(B) {
    if (this.cols !== B.rows)
      throw new Error(`Dimension mismatch: ${this.cols} != ${B.rows}`);
    const result = Array.from({ length: this.rows }, () =>
      Array(this.cols).fill(0)
    );
    for (let i = 0; i < this.rows; i++) {
      for (let k = 0; k < this.cols; k++) {
        const aik = this.get(i, k);
        if (aik === 0) continue; // 疎行列最適化
        for (let j = 0; j < B.cols; j++) {
          result[i][j] += aik * B.get(k, j);
        }
      }
    }
    return new Matrix(result);
  }
}

// ═══════════════════════════════════════════════════════════
// ベクトル演算
// ═══════════════════════════════════════════════════════════

/**
 * ベクトルの内積 a・b
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number}
 */
function dotProduct(a, b) {
  if (a.length !== b.length)
    throw new Error(`Dimension mismatch: ${a.length} != ${b.length}`);
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += a[i] * b[i];
  return sum;
}

/**
 * ユークリッドノルム ||x||₂ = sqrt(Σxᵢ²)
 * @param {number[]} x
 * @returns {number}
 */
function norm(x) {
  let s = 0;
  for (let i = 0; i < x.length; i++) s += x[i] ** 2;
  return Math.sqrt(s);
}

/**
 * 正規化ベクトル x̂ = x / ||x||₂
 * @param {number[]} x
 * @returns {number[]}
 */
function normalize(x) {
  const n = norm(x);
  if (n === 0) return x.map(() => 0);
  return x.map(v => v / n);
}

/**
 * コサイン類似度 cos(θ) = (a・b) / (||a||₂ × ||b||₂)
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number}
 */
function cosineSimilarity(a, b) {
  const dot = dotProduct(a, b);
  const na = norm(a);
  const nb = norm(b);
  if (na === 0 || nb === 0) return 0;
  return dot / (na * nb);
}

/**
 * Hadamard積（要素ごとの積）a ⊙ b
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number[]}
 */
function hadamardProduct(a, b) {
  if (a.length !== b.length)
    throw new Error(`Dimension mismatch: ${a.length} != ${b.length}`);
  return a.map((v, i) => v * b[i]);
}

/**
 * ベクトル加算 a + b
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number[]}
 */
function vecAdd(a, b) {
  return a.map((v, i) => v + b[i]);
}

/**
 * ベクトル減算 a - b
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number[]}
 */
function vecSub(a, b) {
  return a.map((v, i) => v - b[i]);
}

/**
 * ベクトル × スカラー αx
 * @param {number[]} x
 * @param {number} alpha
 * @returns {number[]}
 */
function scalarMul(x, alpha) {
  return x.map(v => v * alpha);
}

// ═══════════════════════════════════════════════════════════
// 行列演算によるRBPスコアリング（研究用明示表現）
// ═══════════════════════════════════════════════════════════

/**
 * 【行列演算】コサイン類似度ベクトル
 *
 * 数学式:
 *   s = CosineSimMatrix(M, v)
 *   sᵢ = cos(Mᵢ, v) = (Mᵢ ・ v) / (||Mᵢ||₂ × ||v||₂)
 *
 * 手順:
 *   1. v̂ = v / ||v||₂                    — 入力の正規化
 *   2. M̂ = NormalizeRows(M)               — 各行の正規化
 *   3. s = diag(M̂ × v̂^T)                 — 対角成分（各行とvの内積）
 *
 * または等価に:
 *   3'. s = M̂ × v̂                        — 行列×ベクトル（行ごとに内積）
 *
 * @param {Matrix} M    - EB_MATRIX (n×d)
 * @param {number[]} v  - ENTRY_VECTOR (d,)
 * @returns {{scores: number[], boxIndices: number[]}}
 *   scores[i] = cos(M[i], v)
 *   boxIndices[i] = i（EBのインデックス）
 */
function cosineSimMatrix(M, v) {
  const vNorm = norm(v);
  if (vNorm === 0) return { scores: M.rows.fill(0), boxIndices: M.rows.fill(0) };

  // Step 1: 入力を正規化
  const vHat = normalize(v);

  // Step 2: 行列の各行を正規化
  const MHat = M.normalizeRows();

  // Step 3: 行列 × ベクトル → 各行とvの内積（＝コサイン類似度）
  // M̂ × v̂ = [cos(M₀,v̂), cos(M₁,v̂), ..., cos(Mₙ₋₁,v̂)]^T
  const scores = MHat.matVecMul(vHat);

  return { scores, boxIndices: M.data.map((_, i) => i) };
}

/**
 * 【行列演算】内積スコアベクトル
 *
 * 数学式:
 *   s = DotProductMatrix(M, v)
 *   sᵢ = Mᵢ ・ v = Σⱼ Mᵢⱼ × vⱼ
 *
 * 手順:
 *   s = M × v^T   （行列×ベクトル）
 *
 * @param {Matrix} M    - EB_MATRIX (n×d)
 * @param {number[]} v  - ENTRY_VECTOR (d,)
 * @returns {{scores: number[], boxIndices: number[]}}
 */
function dotProductMatrix(M, v) {
  // M × v^T → 各行とvの内積
  const scores = M.matVecMul(v);
  return { scores, boxIndices: M.data.map((_, i) => i) };
}

/**
 * 【行列演算】Hadamard変換
 *
 * 数学式:
 *   e' = e ⊙ w   （Hadamard積）
 *
 * BRIDGEのweight_vector適用を行列形式で表現。
 *
 * @param {number[]} e    - 入力ベクトル
 * @param {number[]} w    - 重みベクトル
 * @returns {number[]} 変換後ベクトル
 */
function hadamardTransform(e, w) {
  return hadamardProduct(e, w);
}

/**
 * 【行列演算】BRIDGE変換（行列形式）
 *
 * 数学式:
 *   E' = E ⊙ W   （Hadamard積、Wを各行にbroadcast）
 *   または
 *   E' = E × W_diag   （対角行列による右乗算）
 *
 * ここで W_diag = diag(w) は w の要素を対角成分とする対角行列。
 * E が (n_samples × d)、W_diag が (d × d) のとき:
 *   (n × d) × (d × d) = (n × d)  — 各列 j が w[j] でスケーリングされる
 *
 * @param {Matrix} E    - 入力行列（複数サンプル、各行が1サンプル）
 * @param {number[]} w  - 重みベクトル（列数と一致）
 * @returns {Matrix} 変換後行列
 */
function bridgeTransform(E, w) {
  // 対角行列 Diag(w) を構築 (d × d)
  const WdiagData = Array.from({ length: w.length }, (_, i) =>
    Array(w.length).fill(0).map((_, j) => (i === j ? w[i] : 0))
  );
  const Wdiag = new Matrix(WdiagData);

  // E × W_diag （右乗算：各列を対応する重みでスケーリング）
  return E.matMulFull(Wdiag);
}

/**
 * 【行列演算】全EBへの同時スコアリング（最大化）
 *
 * 数学式:
 *   i* = argmaxᵢ cos(Mᵢ, v)
 *
 * @param {Matrix} M    - EB_MATRIX (n×d)
 * @param {number[]} v  - ENTRY_VECTOR (d,)
 * @param {Object[]} evalBoxes - RBP_EVAL_BOXES（ID取得用）
 * @returns {{bestBox: Object, bestIdx: number, bestScore: number, allScores: number[]}}
 */
function scoreMatrixMax(M, v, evalBoxes) {
  const { scores } = cosineSimMatrix(M, v);

  let bestIdx = 0;
  let bestScore = -Infinity;
  for (let i = 0; i < scores.length; i++) {
    if (scores[i] > bestScore) {
      bestScore = scores[i];
      bestIdx = i;
    }
  }

  return {
    bestBox: evalBoxes[bestIdx],
    bestIdx,
    bestScore,
    allScores: scores
  };
}

/**
 * 【行列演算】内積による全EBスコアリング（最大化）
 *
 * 数学式:
 *   i* = argmaxᵢ (Mᵢ ・ v)
 *
 * @param {Matrix} M    - EB_MATRIX (n×d)
 * @param {number[]} v  - ENTRY_VECTOR (d,)
 * @param {Object[]} evalBoxes - RBP_EVAL_BOXES
 * @returns {{bestBox: Object, bestIdx: number, bestScore: number, allScores: number[]}}
 */
function scoreDotProductMax(M, v, evalBoxes) {
  const { scores } = dotProductMatrix(M, v);

  let bestIdx = 0;
  let bestScore = -Infinity;
  for (let i = 0; i < scores.length; i++) {
    if (scores[i] > bestScore) {
      bestScore = scores[i];
      bestIdx = i;
    }
  }

  return {
    bestBox: evalBoxes[bestIdx],
    bestIdx,
    bestScore,
    allScores: scores
  };
}

// ═══════════════════════════════════════════════════════════
// 出力ヘルパー
// ═══════════════════════════════════════════════════════════

/**
 * 行列をテキストテーブルで表示
 */
function printMatrix(M, label = 'Matrix') {
  console.log(`\n${'='.repeat(60)}`);
  console.log(`${label} (${M.rows}×${M.cols})`);
  console.log('='.repeat(60));

  // カラム幅を自動計算
  const widths = Array(M.cols).fill(0);
  for (let i = 0; i < M.rows; i++) {
    for (let j = 0; j < M.cols; j++) {
      const str = String(parseFloat(M.get(i, j).toFixed(4)));
      widths[j] = Math.max(widths[j], str.length);
    }
  }

  // ヘッダー
  const header = Array(M.cols).fill('').map((_, j) => {
    const colLabels = ['炭疽', '灰かび', 'うどんこ', 'ハダニ', 'ヨトウ', 'タバコガ', 'アザミウマ', 'ワタアブラ', '蚜虫', '白飛'];
    return ((colLabels[j] !== undefined) ? colLabels[j].padEnd(widths[j] + 2) : ('col' + j).padEnd(widths[j] + 2));
  }).join('');
  console.log(header);
  console.log('-'.repeat(header.length));

  // データ
  for (let i = 0; i < M.rows; i++) {
    const row = Array(M.cols).fill('').map((_, j) => {
      const val = M.get(i, j);
      return (val === Math.floor(val) ? val.toString() : parseFloat(val.toFixed(4)).toString()).padEnd(widths[j] + 2);
    }).join('');
    console.log(`[${String(i).padStart(2)}] ${row}`);
  }
  console.log(`\nフローベニウスノルム: ${parseFloat(M.frobeniusNorm().toFixed(6))}`);
}

/**
 * スコアベクトルを表示
 */
function printScores(label, scores, boxIds) {
  console.log(`\n${label}`);
  console.log('-'.repeat(40));
  const maxScore = Math.max(...scores);
  for (let i = 0; i < scores.length; i++) {
    const marker = scores[i] === maxScore ? ' ◀ BEST' : '';
    console.log(`  ${String(boxIds[i]).padEnd(8)} ${scores[i].toFixed(6)}${marker}`);
  }
}

// ═══════════════════════════════════════════════════════════
// エクスポート
// ═══════════════════════════════════════════════════════════

module.exports = {
  // 基本型
  Matrix,

  // ベクトル演算
  dotProduct,
  norm,
  normalize,
  cosineSimilarity,
  hadamardProduct,
  vecAdd,
  vecSub,
  scalarMul,

  // 行列演算によるRBPスコアリング
  cosineSimMatrix,
  dotProductMatrix,
  hadamardTransform,
  bridgeTransform,
  scoreMatrixMax,
  scoreDotProductMax,

  // ヘルパー
  printMatrix: printMatrix,
  printScores: printScores
};
