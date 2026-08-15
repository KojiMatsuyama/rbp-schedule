// framework/engine.js — RBP汎用行列演算エンジン（ドメイン非依存）
// 病害虫・薬剤に限らず、任意のドメインのRBP判断で共通して使うベクトル演算。
// ブラウザで<script src="framework/engine.js">として読み込まれるグローバルスクリプト。

function dotProduct(a, b) {
  return a.reduce((sum, x, i) => sum + x * b[i], 0);
}

function norm(v) {
  return Math.sqrt(v.reduce((s, x) => s + x * x, 0));
}

function cosineSimilarity(a, b) {
  const d = dotProduct(a, b);
  const na = norm(a);
  const nb = norm(b);
  if (na === 0 || nb === 0) return 0;
  return d / (na * nb);
}

// 行列演算によるコサイン類似度（Method B: Broadcast-Hadamard-Reduce）
// 数学式: s = M̂ × v̂^T  (M̂ = NormalizeRows(M), v̂ = Normalize(v))
function cosineSimMatrix(M, v) {
  const vMag = norm(v);
  if (vMag === 0) return { scores: M.map(() => 0), indices: M.map((_, i) => i) };

  const vHat = v.map(x => x / vMag);

  const MHat = M.map(row => {
    const mag = norm(row);
    return mag === 0 ? row.map(() => 0) : row.map(x => x / mag);
  });

  const scores = MHat.map(row => dotProduct(row, vHat));

  return { scores, indices: M.map((_, i) => i) };
}
