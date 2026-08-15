// rbp/eval_box_registry.js — 要求評価RBP：EVAL_BOXの自動登録
// data/diseases.js（DISEASES）, data/eval_boxes.js（EB_VECTORS/EB_MATRIX/EB_NAMES）,
// framework/mirror.js（matchExactBox）に依存。
//
// 設計方針（ユーザー確認済み）:
//   病害虫の組み合わせは「概念操作」であり、有限のEVAL_BOXを事前に網羅しきることはできない
//   （防除を1回飛ばして病害虫が蓄積する、想定外の病害虫が例外的に発生する等）。
//   そのため、既存のどのEVAL_BOXとも完全一致しない入力ベクトルは、確認なしでその場を
//   新しいEVAL_BOXとして自動登録してよい。これは薬剤（仕様決定RBP側）とは対照的な扱いで、
//   薬剤は現実の物理的制約（実在する商品）なので自動追加の対象にはならない。

// 既存ID（"EB-01"等）から数値部分の最大値を取り、+1したIDを返す。
// 欠番（例: EB-05, EB-10）は意図的に埋めない前提のシンプルな採番。
function computeNextEvalBoxId(existingIds) {
  const nums = existingIds
    .map(id => {
      const m = /^EB-(\d+)$/.exec(id);
      return m ? parseInt(m[1], 10) : null;
    })
    .filter(n => n !== null);
  const maxNum = nums.length > 0 ? Math.max(...nums) : 0;
  const nextNum = maxNum + 1;
  return `EB-${String(nextNum).padStart(2, '0')}`;
}

// ベクトルのうち値が立っている次元のDISEASES名を"+"で連結する（既存EB_NAMESの命名規則に合わせる）。
function buildEvalBoxName(vector) {
  return DISEASES
    .filter((d, i) => vector[i] > 0)
    .map(d => d.name)
    .join('+');
}

// 入力ベクトルを既存EVAL_BOXへ完全一致判定し、一致がなければその場で新規EVAL_BOXとして
// EB_VECTORS/EB_MATRIX/EB_NAMESへ直接登録する。
// 戻り値:
//   { status:'OK', id, name, isNew }                     — 判定成功（既存 or 新規登録）
//   { status:'MODEL_DEFINITION_ERROR', matchedIds }        — 同一ベクトルが複数BOXに重複登録済み
function classifyAndRegisterVector(vector) {
  const existingIds = Object.keys(EB_VECTORS);
  const result = matchExactBox(vector, EB_MATRIX, existingIds);

  if (result.status === 'MODEL_DEFINITION_ERROR') {
    return result;
  }

  if (result.status === 'OK') {
    return { status: 'OK', id: result.id, name: EB_NAMES[result.id], isNew: false };
  }

  // UNDEFINED: 新規EVAL_BOXとして登録する
  const newId = computeNextEvalBoxId(existingIds);
  const newName = buildEvalBoxName(vector);
  const newVector = vector.slice();

  EB_VECTORS[newId] = newVector;
  EB_MATRIX.push(newVector);
  EB_NAMES[newId] = newName;

  return { status: 'OK', id: newId, name: newName, isNew: true };
}
