// rbp/spec_bridges.js — 仕様決定RBP：SPEC_LINE／SPEC_BRIDGE定義
// 22.薬剤仕様決定RBP設計書 §2〜§3 の実装。
// framework/rbp_core.js（runLineThroughBridges）, framework/engine.js（dotProduct）,
// data/pesticides.js（PESTICIDE_DB）, rbp/spec_matching.js（hasMixingConflict）に依存。
//
// 水路構造:
//   EB_VECTOR（水源）
//     → SPEC_LINE（薬剤ごとの縦パイプ・67本）
//     → SPEC-BRIDGE-TARGET     (L1: ターゲット一致 …… 不一致は遮断)
//     → SPEC-BRIDGE-USAGE      (L2: 散布回数上限 …… 到達は遮断)
//     → SPEC-BRIDGE-PHI        (L3: PHI残日数 ……… 不足は減衰0.5)
//     → SPEC-BRIDGE-ROTATION   (L4: 系統ローテーション… 連続使用は減衰0.3)
//     → SPEC-BRIDGE-MIXING     (L5: 前回散布薬剤との混用禁止 … 該当は遮断)
//     → SPEC-BRIDGE-TOXICITY   (L6: 毒性区分 ……… 劇物は減衰0.7)
//     → 水流ありの最終ライン → セット列挙 → SPEC-BRIDGE-MIXING-SET(L5.5相当) → SPEC_BOX

const NON_ROTATION_SYSTEM_CODES = ['MIX', 'PHYSICAL'];

// 各BRIDGEはHadamard積で通水ベクトルに乗じる「一様重みベクトル」（全次元同一値）を返す。
// ebVector.map(() => 定数) を直書きすると「何を作っているか」がブリッジ定義ごとに埋もれるため、
// 意味のある名前を持つ生成ヘルパーに切り出す。三項演算子側の条件式（散布回数上限か等の
// 業務判断そのもの）はIF/THENとして残し、各BRIDGEの description / reason_fn / warning_fn で
// 意図を明記する。
const fullPass = (dim) => dim.map(() => 1);       // 通過（重み1：水はそのまま流れる）
const fullBlock = (dim) => dim.map(() => 0);      // 遮断（重み0：水を完全に止める）
const attenuate = (dim, factor) => dim.map(() => factor); // 減衰（0<factor<1：水量を減らして通す）

const SPEC_BRIDGES = [
  {
    id: 'SPEC-BRIDGE-TARGET',
    level: 1,
    direction: 'forward',
    weight_vector_fn: ({ ebVector, targetMatch }) => {
      // 薬剤のターゲットと要求（EBベクトル）が1件も重ならないなら、この薬剤は無関係。
      // targetMatch = TARGET_MATRIX × ebVector（rbp/spec_matching.js: computeTargetMatchVector）
      // を runAllSpecLines が1回だけ計算し、薬剤ごとの成分をctxへ渡したもの。
      const targetsRequestedPest = targetMatch > 0;
      return targetsRequestedPest ? fullPass(ebVector) : fullBlock(ebVector);
    },
    reason_fn: ({ pesticide }) => `${pesticide.name}: 対象病害虫が要求（EBベクトル）と一致しない`,
    description: 'EBベクトルとターゲットが一致しない薬剤を遮断',
  },
  {
    id: 'SPEC-BRIDGE-USAGE',
    level: 2,
    direction: 'forward',
    weight_vector_fn: ({ pesticide, ebVector, safetyVector }) => {
      const usageCount = safetyVector.usageState[pesticide.id] || 0;
      const usageLimitReached = usageCount >= pesticide.maxApplications;
      return usageLimitReached ? fullBlock(ebVector) : fullPass(ebVector);
    },
    reason_fn: ({ pesticide, safetyVector }) => {
      const usageCount = safetyVector.usageState[pesticide.id] || 0;
      return `散布回数上限に到達（${usageCount}/${pesticide.maxApplications === Infinity ? '無制限' : pesticide.maxApplications}回）`;
    },
    description: '散布回数上限に達した薬剤を遮断',
  },
  {
    id: 'SPEC-BRIDGE-PHI',
    level: 3,
    direction: 'forward',
    weight_vector_fn: ({ pesticide, ebVector, safetyVector }) => {
      const phiNotYetSatisfied = safetyVector.intervalDays !== null && safetyVector.intervalDays < pesticide.phiDays;
      return phiNotYetSatisfied ? attenuate(ebVector, 0.5) : fullPass(ebVector);
    },
    penalty: { axis: 'safety', delta: -10 },
    warning_fn: ({ pesticide, safetyVector }) =>
      `${pesticide.name}: PHI残日数要確認（前回散布から${safetyVector.intervalDays}日、PHI${pesticide.phiDays}日）`,
    description: 'PHI残日数を満たさない薬剤を減衰（完全遮断ではない）',
  },
  {
    id: 'SPEC-BRIDGE-ROTATION',
    level: 4,
    direction: 'forward',
    weight_vector_fn: ({ pesticide, ebVector, safetyVector }) => {
      const rotCount = safetyVector.rotationState[pesticide.systemCode] || 0;
      const isNonRotationSystem = NON_ROTATION_SYSTEM_CODES.includes(pesticide.systemCode);
      const sameSystemOveruse = rotCount >= 2 && !isNonRotationSystem;
      return sameSystemOveruse ? attenuate(ebVector, 0.3) : fullPass(ebVector);
    },
    penalty: { axis: 'resistance', delta: -15 },
    warning_fn: ({ pesticide, safetyVector }) => {
      const rotCount = safetyVector.rotationState[pesticide.systemCode] || 0;
      return `${pesticide.name}: 同系統（${pesticide.system}）を${rotCount}回連続使用中（抵抗性リスク）`;
    },
    description: '同系統連続使用中の薬剤を減衰（抵抗性リスク管理）',
  },
  {
    id: 'SPEC-BRIDGE-MIXING',
    level: 5,
    direction: 'forward',
    weight_vector_fn: ({ pesticide, ebVector, safetyVector }) => {
      const conflictsWithLastSpray = safetyVector.lastPesticideIds.some(lastId => hasMixingConflict(pesticide, lastId));
      return conflictsWithLastSpray ? fullBlock(ebVector) : fullPass(ebVector);
    },
    reason_fn: ({ pesticide, safetyVector }) => {
      const names = safetyVector.lastPesticideIds
        .filter(id => hasMixingConflict(pesticide, id))
        .map(id => (PESTICIDE_DB.find(x => x.id === id) || {}).name);
      return `${pesticide.name}は前回散布薬剤（${names.join('、')}）と混用不可`;
    },
    description: '前回散布薬剤との混用禁止で遮断',
  },
  {
    id: 'SPEC-BRIDGE-TOXICITY',
    level: 6,
    direction: 'forward',
    weight_vector_fn: ({ pesticide, ebVector }) => {
      const isHighlyToxic = pesticide.toxicityClass === '劇物';
      return isHighlyToxic ? attenuate(ebVector, 0.7) : fullPass(ebVector);
    },
    penalty: { axis: 'safety', delta: -8 },
    warning_fn: ({ pesticide }) => `${pesticide.name}: 劇物区分`,
    description: '劇物区分の薬剤を減衰（推奨はしないが禁止はしない）',
  },
];

const SPEC_BRIDGE_BY_ID = Object.fromEntries(SPEC_BRIDGES.map(b => [b.id, b]));

// セット単位の特殊ゲート（設計書§3.4）: セット内薬剤間の混用禁止。
// setHasInternalMixingConflict / buildMixingReason は rbp/prescription.js 側の定義を実行時に参照する。
const SPEC_BRIDGE_MIXING_SET = {
  id: 'SPEC-BRIDGE-MIXING-SET',
  level: 5.5,
  direction: 'forward',
  weight_vector_fn: ({ set, ebVector }) => {
    const isPairWithInternalMixingConflict = set.length === 2 && setHasInternalMixingConflict(set);
    return isPairWithInternalMixingConflict ? fullBlock(ebVector) : fullPass(ebVector);
  },
  reasons_fn: ({ set }) => (set.length === 2 ? buildMixingReason(set[0], set[1]) : []),
  description: 'セット内薬剤間の混用禁止で遮断',
};

// 1本のSPEC_LINE（=1薬剤の縦パイプ）に水源（ebVector）から通水する
// targetMatch: TARGET_MATRIX × ebVector のうちこの薬剤に対応する成分（行列積の結果を再利用）
function runSpecLine(pesticide, ebVector, safetyVector, targetMatch) {
  const ctx = { pesticide, ebVector, safetyVector, targetMatch };
  const result = runLineThroughBridges(ebVector, SPEC_BRIDGES, ctx);
  return { pesticide, ...result };
}

// 全SPEC_LINE（PESTICIDE_DBの全薬剤）に通水し、ライン別の到達状況を返す
function runAllSpecLines(ebVector, safetyVector) {
  // c = TARGET_MATRIX × ebVector を1回だけ計算し、全SPEC_LINEで共有する（行列積の単一情報源）
  const targetMatchVector = computeTargetMatchVector(ebVector);
  return PESTICIDE_DB.map((p, i) => runSpecLine(p, ebVector, safetyVector, targetMatchVector[i]));
}

// セット単位ゲートの通水判定
function runSetGate(set, ebVector, safetyVector) {
  const weight = SPEC_BRIDGE_MIXING_SET.weight_vector_fn({ set, ebVector, safetyVector });
  const blocked = weight.every(w => w === 0);
  return { blocked, reasons: blocked ? SPEC_BRIDGE_MIXING_SET.reasons_fn({ set }) : [] };
}
