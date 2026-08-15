// rbp/spec_matching.js — 仕様決定RBP：EB分類ラッパー＋混用禁止制約行列
// data/pesticides.js（PESTICIDE_DB）, data/eval_boxes.js（EB_MATRIX, EB_NAMES）,
// framework/engine.js（dotProduct, cosineSimMatrix）, framework/mirror.js（classifyAgainstBoxes）に依存。
//
// 旧・候補選択UI（renderSpecBox）は呼び出し元がなく完全なデッドコードだったため削除済み。
// matchCandidates / scoreCandidate / rankPrescriptions / buildSpecBox は
// rbp/prescription.js の buildPrescriptionSet 系統（SPEC_BRIDGE通水方式）と
// 同一の判定基準（散布回数上限・PHI・ローテーション・混用禁止・毒性）を
// if-elseチェーンで二重実装していたもので、ライブパスからは一切参照されていなかった。

// 完全一致によるEVAL_BOX分類（読み取り専用）。rbp/eval_box_registry.jsのclassifyAndRegisterVector
// と異なり、未定義パターンに遭遇しても新規BOXを登録しない。
// schedule_app.html の一覧表示・CSVエクスポート（表示のみ）から呼ばれるライブコード。
// 処方決定（buildPrescriptionSet）へはフィードバックしない、表示専用の独立系統。
function classifyVector(v) {
  const result = matchExactBox(v, EB_MATRIX, Object.keys(EB_VECTORS));
  if (result.status !== 'OK') return result;
  return { status: 'OK', id: result.id, name: EB_NAMES[result.id] };
}

// ── 混用禁止制約行列（MIXING_CONFLICT_MATRIX）のコンパイル ──
// 「薬剤iと薬剤jは混用可能か」という67×67の関係は、散布のたびに文字列比較で
// 再計算するようなものではなく、PESTICIDE_DBが確定した時点で一意に定まる定数関係。
// そのため起動時に1回だけ0/1行列としてコンパイルし、以降は行列参照のみで判定する
// （DSLコンパイル責任＝R1に相当。ここでのループは「候補集合を毎回手続き的に走査する」
//   パターンとは性質が異なり、モデル定義の構築処理として正当）。
const PESTICIDE_INDEX_BY_ID = Object.fromEntries(PESTICIDE_DB.map((p, i) => [p.id, i]));

function computesMixingConflict(pesticideA, pesticideB) {
  const aTargets = pesticideA.mixingBanTargets || [];
  const bTargets = pesticideB.mixingBanTargets || [];
  const aBansB = aTargets.some(t => pesticideB.system.includes(t) || pesticideB.name.includes(t));
  const bBansA = bTargets.some(t => pesticideA.system.includes(t) || pesticideA.name.includes(t));
  return aBansB || bBansA;
}

const MIXING_CONFLICT_MATRIX = PESTICIDE_DB.map(pa =>
  PESTICIDE_DB.map(pb => (computesMixingConflict(pa, pb) ? 1 : 0))
);

// 薬剤同士が混用禁止関係にあるかを行列参照で判定する。
// シグネチャは従来通り(pesticide, otherId)を維持し、呼び出し元
// （rbp/prescription.js, rbp/spec_bridges.js）は無変更で動作する。
function hasMixingConflict(pesticide, otherId) {
  const i = PESTICIDE_INDEX_BY_ID[pesticide.id];
  const j = PESTICIDE_INDEX_BY_ID[otherId];
  if (i === undefined || j === undefined) return false;
  return MIXING_CONFLICT_MATRIX[i][j] === 1;
}

// ── ターゲット一致行列（TARGET_MATRIX）のコンパイル ──
// 22.薬剤仕様決定RBP設計書 SPEC-BRIDGE-TARGET（L1）が行っている
// 「dotProduct(ebVector, pesticide.targetVector)」の判定を、薬剤ごとに毎回計算する代わりに、
// 67×10のターゲット行列を起動時に1回だけコンパイルし、以降は行列×ベクトル積で一括計算する。
// c = TARGET_MATRIX × ebVector （67薬剤 × 1回の呼び出し = 全薬剤のターゲット一致度）
const TARGET_MATRIX = PESTICIDE_DB.map(p => p.targetVector);

function computeTargetMatchVector(ebVector) {
  return TARGET_MATRIX.map(row => dotProduct(row, ebVector));
}
