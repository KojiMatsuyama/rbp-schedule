// framework/mirror.js — Mirror-ID汎用計算（ドメイン非依存）
// EVAL_BOX（要求の意味構造）と SPEC_BOX（仕様の意味構造）の意味距離を計算する。
// 病害虫・薬剤に限らず、「ベクトル化された候補群からEVAL_BOXへの距離が最小/最大のものを選ぶ」
// という判断構造であればどのドメインでも使える。
// framework/engine.js の後に<script>で読み込まれる前提（cosineSimMatrix, dotProductを使用）。

// 完全一致によるBOX判定（22.薬剤仕様決定RBP設計書・RBP_PRINCIPLES.md §7.1が求める判定方式）。
// コサイン類似度による最近傍選択（classifyAgainstBoxes）とは異なり、
// 「一致するBOXが存在するか」を厳密に判定する。中間の類似度は評価しない。
//   0件一致  → UNDEFINED（判定不能。新規BOXとして追加され得る状態）
//   2件以上  → MODEL_DEFINITION_ERROR（同一ベクトルが複数BOXに重複登録されているデータ不整合）
//   1件一致  → OK
function matchExactBox(v, boxMatrix, boxLabels) {
  const matches = [];
  for (let i = 0; i < boxMatrix.length; i++) {
    const row = boxMatrix[i];
    if (row.length === v.length && row.every((x, j) => x === v[j])) {
      matches.push(i);
    }
  }
  if (matches.length === 0) {
    return { status: 'UNDEFINED' };
  }
  if (matches.length >= 2) {
    return { status: 'MODEL_DEFINITION_ERROR', matchedIds: matches.map(i => boxLabels[i]) };
  }
  return { status: 'OK', index: matches[0], id: boxLabels[matches[0]] };
}

// 汎用EVAL_BOX分類：候補行列 boxMatrix（n×d）の中から入力ベクトル v に最も近いBOXを選ぶ。
// boxLabels[i] / boxNames[i] は boxMatrix[i] に対応するID・名称。
function classifyAgainstBoxes(v, boxMatrix, boxLabels, boxNames) {
  const { scores: cosScores } = cosineSimMatrix(boxMatrix, v);
  const dotScores = boxMatrix.map(row => dotProduct(v, row));

  let bestLabel = null;
  let bestCosine = -Infinity;
  let bestDot = -Infinity;
  const allScores = {};

  for (let i = 0; i < boxLabels.length; i++) {
    const label = boxLabels[i];
    allScores[label] = { cosine: cosScores[i], dot: dotScores[i] };
    if (cosScores[i] > bestCosine) {
      bestCosine = cosScores[i];
      bestLabel = label;
      bestDot = dotScores[i];
    }
  }

  return {
    eb: bestLabel,
    name: (boxNames && boxNames[bestLabel]) || '',
    cosine: bestCosine,
    dot: bestDot,
    allScores,
  };
}

// Mirror-ID: 候補セット（items）のunion coverageベクトルとEVAL_BOXベクトルとの適合度。
// items内のいずれかが対象次元iをカバーしていれば union[i] = 1。
// getTargetVector: item -> ベクトル を取り出す関数（ドメインごとのフィールド名に対応するため）
function computeUnionCoverage(items, evalVector, getTargetVector) {
  return evalVector.map((_, i) =>
    items.some(item => getTargetVector(item)[i] > 0) ? 1 : 0
  );
}
