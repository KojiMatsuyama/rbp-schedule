// rbp/prescription.js — 仕様決定RBP：処方セット（1剤/2剤）の自動決定
// 「候補を選ばせる」のではなく「処方セットを1つ決定する」仕様決定RBPの中核。
// 22.薬剤仕様決定RBP設計書に基づき、除外判定はif-elseではなく
// SPEC_BRIDGE通水（rbp/spec_bridges.js → framework/rbp_core.js）で行う。
// data/pesticides.js（PESTICIDE_DB）, rbp/spec_matching.js（hasMixingConflict）,
// framework/engine.js（dotProduct）に依存。
// NON_ROTATION_SYSTEM_CODES は rbp/spec_bridges.js で定義。

function setHasInternalMixingConflict(pesticideList) {
  if (pesticideList.length < 2) return false;
  const [a, b] = pesticideList;
  return hasMixingConflict(a, b.id);
}

function buildMixingReason(a, b) {
  const reasons = [];
  const aTargets = a.mixingBanTargets || [];
  const bTargets = b.mixingBanTargets || [];

  const aBanTarget = aTargets.find(t => b.system.includes(t) || b.name.includes(t));
  if (aBanTarget) reasons.push(`${a.name}は${b.name}（${aBanTarget}）と混用不可`);

  const bBanTarget = bTargets.find(t => a.system.includes(t) || a.name.includes(t));
  if (bBanTarget) reasons.push(`${b.name}は${a.name}（${bBanTarget}）と混用不可`);

  return reasons;
}

// 設計書§7.2 RBP_OUT.bridge_trace: 各薬剤のBRIDGE通過履歴
function buildBridgeTrace(lineResults) {
  return lineResults.map(r => ({
    pesticide: r.pesticide.id,
    levels: r.trace.map(t => t.level),
    weights: r.trace.map(t => t.weight),
    blocked: r.blocked,
    blockedAt: r.blockedAt,
  }));
}

function scorePrescriptionSet(pesticides, ebVector, safetyVector, pool, lineById) {
  // ── 効果：union coverage（重複対象は加点せず、異なる対象のカバーだけが加点になる） ──
  const unionVector = ebVector.map((_, i) => pesticides.some(p => p.targetVector[i] > 0) ? 1 : 0);
  const matchCount = dotProduct(unionVector, ebVector);
  const targetSum = ebVector.reduce((s, v) => s + v, 0);
  const coverageRatio = targetSum > 0 ? matchCount / targetSum : 0;
  // Mirror-ID: セットのunion coverageベクトルと要求ベクトル（EB_VECTOR）とのコサイン類似度。
  // 22.薬剤仕様決定RBP設計書 §5 が定義する適合度そのもので、セット選定の主基準として使う
  // （framework/engine.js の cosineSimilarity を再利用）。
  const mirrorId = cosineSimilarity(unionVector, ebVector);
  const effectivenessScore = mirrorId * 10 + coverageRatio * 5;

  // BRIDGE通過履歴から、セット内全薬剤×指定軸の減衰イベント（0<weight<1で通過したBRIDGE）を
  // 一括で取り出す。減点・警告文の定義はBRIDGE側（penalty / warning_fn）が単一の情報源。
  // 「pesticides配列を舐めてスコアに加算していく」手続きではなく、
  // 「セット全体のペナルティベクトルを構築 → 総和を取る」という集約演算として表現する。
  function attenuationEventsForAxis(axis) {
    return pesticides.flatMap(p => {
      const line = lineById.get(p.id);
      if (!line) return [];
      return line.trace
        .filter(t => t.attenuated)
        .map(t => SPEC_BRIDGE_BY_ID[t.bridgeId])
        .filter(b => b.penalty && b.penalty.axis === axis)
        .map(b => ({ pesticide: p, bridge: b }));
    });
  }

  // ── 安全性：L3（PHI）・L6（毒性）の減衰イベントをペナルティベクトルとして集約 ──
  const safetyEvents = attenuationEventsForAxis('safety');
  const safetyPenaltyVector = safetyEvents.map(e => e.bridge.penalty.delta);
  const safetyWarnings = safetyEvents.map(e => e.bridge.warning_fn({ pesticide: e.pesticide, safetyVector }));
  const safetyScore = Math.max(0, 20 + safetyPenaltyVector.reduce((sum, delta) => sum + delta, 0));

  // ── 抵抗性リスク：L4（系統ローテーション）の減衰イベントをペナルティベクトルとして集約 ──
  const resistanceEvents = attenuationEventsForAxis('resistance');
  const resistancePenaltyVector = resistanceEvents.map(e => e.bridge.penalty.delta);
  const resistanceWarnings = resistanceEvents.map(e => e.bridge.warning_fn({ pesticide: e.pesticide, safetyVector }));

  let resistanceNote = '';
  const bonusWarnings = [];
  // 2剤セット限定の異系統ボーナス判定：
  // これは「候補集合全体への一括演算」ではなく、2剤という固定サイズの組み合わせに対する
  // 具体的な業務ルール（同一系統か異系統か、単剤で代替できる冗長な組み合わせでないか）。
  // 行列・ベクトル演算に無理に押し込めるより、意味のある変数名のIF/THENとして残し、
  // 各分岐の業務的な意図をコメントで明示する方が人間にとって理解しやすい。
  if (pesticides.length === 2) {
    const [a, b] = pesticides;
    // MIX/PHYSICAL系統は「ローテーション対象外」＝薬剤抵抗性の観点で系統を持たない扱い
    const aParticipatesInRotation = !NON_ROTATION_SYSTEM_CODES.includes(a.systemCode);
    const bParticipatesInRotation = !NON_ROTATION_SYSTEM_CODES.includes(b.systemCode);
    const isSameSystemCombo = a.systemCode === b.systemCode;
    // 単剤のいずれかが、このセットと同等以上のunion coverageを既に持っているか
    // （＝2剤にしても効果が純増していない＝異系統ボーナスに値する組み合わせではない）
    const isRedundantWithSoloAlternative = (pool || []).some(p =>
      dotProduct(p.targetVector, ebVector) >= matchCount
    );

    if (aParticipatesInRotation && bParticipatesInRotation) {
      if (isSameSystemCombo) {
        resistanceNote = '同一系統の組み合わせ：抵抗性リスク低減効果なし';
        bonusWarnings.push(resistanceNote);
      } else if (!isRedundantWithSoloAlternative) {
        resistanceNote = `異なる系統（${a.systemCode}／${b.systemCode}）の組み合わせ：抵抗性管理上有効`;
      }
    }
  }
  // 異系統ボーナス／同一系統ペナルティも他のBRIDGE減衰と同じくペナルティベクトルの要素として合算する
  const comboAdjustment = resistanceNote.includes('低減効果なし') ? -20
    : resistanceNote.includes('抵抗性管理上有効') ? 10
    : 0;
  const resistanceScore = Math.max(
    0,
    15 + resistancePenaltyVector.reduce((sum, delta) => sum + delta, 0) + comboAdjustment
  );

  const warnings = [...safetyWarnings, ...resistanceWarnings, ...bonusWarnings];
  const totalScore = effectivenessScore + safetyScore + resistanceScore;

  return {
    pesticides,
    isCombo: pesticides.length > 1,
    matchCount,
    coverageRatio,
    mirrorId,
    effectivenessScore,
    safetyScore,
    resistanceScore,
    totalScore,
    warnings,
    breakdown: {
      effectiveness: { raw: effectivenessScore, coverageRatio, matchCount, targetSum, mirrorId },
      safety: { raw: safetyScore, warnings: warnings.filter(w => w.includes('PHI') || w.includes('劇物')) },
      resistance: { raw: resistanceScore, note: resistanceNote },
      mixing: { ok: true },
    },
  };
}

function buildPrescriptionSet(ebVector, safetyVector) {
  // ── 通水：全SPEC_LINEをSPEC_BRIDGE列（L1〜L6）に通す ──
  const lineResults = runAllSpecLines(ebVector, safetyVector);
  const bridgeTrace = buildBridgeTrace(lineResults);

  // L1（ターゲット不一致）で遮断されたラインは水源に接続がない扱いで、除外一覧にも載せない
  const connected = lineResults.filter(r => !(r.blocked && r.blockedAt === 'SPEC-BRIDGE-TARGET'));

  // 要求された病害虫のうち、そもそもPESTICIDE_DBに対応薬剤が1つも定義されていない次元。
  // L1で全滅（connected.length===0）した場合の原因説明に使う。
  function findUndefinedDims() {
    return ebVector
      .map((v, i) => (v > 0 && !PESTICIDE_DB.some(p => p.targetVector[i] > 0)) ? i : -1)
      .filter(i => i >= 0);
  }

  // 対象病害虫に対応する薬剤がPESTICIDE_DBに一件も定義されていない（L1で全滅）。
  // 「薬剤は存在するが制約で選択不能」（L2〜L6起因のALL_BLOCKED_BY_CONSTRAINTS）とは
  // 原因が異なるため、ユーザー確認済みの方針通りアラートを分離する。
  if (connected.length === 0) {
    return {
      best: null,
      alternatives: [],
      excludedSets: [],
      excludedIndividual: [],
      bridgeTrace,
      status: 'NO_PESTICIDE_DEFINED',
      undefinedDims: findUndefinedDims(),
    };
  }

  // ── 途中のBRIDGEで遮断されたライン（L2:散布回数上限／L5:前回薬剤との混用禁止）を回収 ──
  const excludedIndividual = connected
    .filter(r => r.blocked)
    .map(r => ({ pesticides: [r.pesticide], exclusionReasons: [r.blockReason] }));

  // 冗長性判定（異系統ボーナスの要否）用プール：L2（散布回数）まで通過したライン。
  // L5で遮断されたラインも「単剤としての効果比較」には参加する（旧実装と同一の母集団）。
  const pool = connected
    .filter(r => !(r.blocked && r.blockedAt === 'SPEC-BRIDGE-USAGE'))
    .map(r => r.pesticide);

  // ── セット列挙対象：最終ライン（L6）まで水が届いたラインのみ ──
  const flowing = connected.filter(r => !r.blocked);
  const lineById = new Map(flowing.map(r => [r.pesticide.id, r]));

  // 薬剤は存在するが、使用回数上限・PHI・混用禁止等（L2〜L6）で全件選択不能になった状態。
  // 「そもそも対応薬剤が定義されていない」（NO_PESTICIDE_DEFINED）とは原因が異なるため、
  // ユーザー確認済みの方針通りアラートを分離する（診断結果.txtで指摘した曖昧さの是正）。
  if (flowing.length === 0) {
    return { best: null, alternatives: [], excludedSets: [], excludedIndividual, bridgeTrace, status: 'ALL_BLOCKED_BY_CONSTRAINTS' };
  }

  // ── 候補セット列挙（1剤・2剤） ──
  // 「到達した薬剤n本から2本を選ぶ組み合わせ」の列挙であり、行列演算に置き換えるべき
  // 一括操作ではなく本質的に列挙処理（nが大きくても67C2=2211件が上限で問題にならない）。
  const candidateSets = flowing.map(r => [r.pesticide]);
  for (let i = 0; i < flowing.length; i++) {
    for (let j = i + 1; j < flowing.length; j++) {
      candidateSets.push([flowing[i].pesticide, flowing[j].pesticide]);
    }
  }

  // ── セット単位ゲート：SPEC-BRIDGE-MIXING-SET（セット内混用禁止） ──
  const validSets = [];
  const excludedSets = [];
  for (const set of candidateSets) {
    const gate = runSetGate(set, ebVector, safetyVector);
    if (gate.blocked) {
      excludedSets.push({ pesticides: set, exclusionReasons: gate.reasons });
    } else {
      validSets.push(set);
    }
  }

  // ── スコアリング（減衰イベントはBRIDGE通過履歴から復元） ──
  const scored = validSets.map(set => scorePrescriptionSet(set, ebVector, safetyVector, pool, lineById));

  // ── Mirror-ID（コサイン類似度）を主基準にセットを選定（タイブレーク付き） ──
  // 薬剤は現実の物理的制約（実在する登録薬剤）なので、要求ベクトルに最も近い
  // union coverageを持つ既存セットを選ぶ、という設計書§5のMirror-ID選定方針に従う。
  // 安全性・抵抗性スコア（BRIDGE減衰由来のペナルティ）はMirror-ID同点時のタイブレークとして働く。
  scored.sort((a, b) => {
    if (b.mirrorId !== a.mirrorId) return b.mirrorId - a.mirrorId;
    if (b.totalScore !== a.totalScore) return b.totalScore - a.totalScore;
    if (a.pesticides.length !== b.pesticides.length) return a.pesticides.length - b.pesticides.length;
    const aKey = a.pesticides.map(p => p.id).sort().join(',');
    const bKey = b.pesticides.map(p => p.id).sort().join(',');
    return aKey.localeCompare(bKey);
  });

  // SPEC-BRIDGE-MIXING-SETは2剤セット（set.length===2）のみを対象とするゲートなので、
  // flowing.length > 0 である限り単剤セットは必ずvalidSetsに残り、scoredは空にならない。
  const best = scored[0];
  const alternatives = scored.slice(1);

  return {
    best,
    alternatives,
    excludedSets,
    excludedIndividual,
    bridgeTrace,
    status: 'SUCCESS',
  };
}
