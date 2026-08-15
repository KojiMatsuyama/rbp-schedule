// framework/rbp_core.js — RBP水路伝播コア（ドメイン非依存）
// LINE（縦パイプ）をBRIDGE（横パイプ＝逆止弁付き開閉ゲート）の列に通し、
// Hadamard積で水（ベクトル）を変形させながら伝播させる汎用エンジン。
// 病害虫・薬剤などのドメイン概念は一切知らない。
//
// BRIDGEの形:
//   {
//     id: string,
//     level: number,                       // 通過順（strictly increasing = 循環なし制約）
//     direction: 'forward',                // 逆止弁制約（必須）
//     weight_vector_fn: (ctx) => number[], // 動的重みベクトル（全0=遮断、0<w<1=減衰、全1=通過）
//     reason_fn?: (ctx) => string,         // 遮断時の理由文
//     penalty?: { axis: string, delta: number }, // 減衰時のスコア減点（ドメイン層が解釈）
//     warning_fn?: (ctx) => string,        // 減衰時の警告文
//     description: string,
//   }

function hadamard(a, b) {
  return a.map((x, i) => x * b[i]);
}

function isZeroVector(v) {
  return v.every(x => x === 0);
}

// 1本のLINEに水を通す。
//   initialFlow: 水源からLINEに入る初期流量ベクトル
//   bridges:     通過するBRIDGE配列（level昇順に実行される）
//   ctx:         weight_vector_fn / reason_fn / warning_fn に渡すドメイン固有コンテキスト
// 戻り値:
//   {
//     flow,        // 最終流量ベクトル（遮断時は全0）
//     blocked,     // 途中で水流が止まったか
//     blockedAt,   // 遮断したBRIDGEのid（なければnull）
//     blockReason, // 遮断理由文（なければnull）
//     trace,       // 通過履歴 [{ bridgeId, level, weight, passed, attenuated }]
//   }
function runLineThroughBridges(initialFlow, bridges, ctx) {
  const sorted = [...bridges].sort((a, b) => a.level - b.level);
  let flow = initialFlow.slice();
  const trace = [];
  let prevLevel = -Infinity;

  for (const bridge of sorted) {
    if (bridge.direction !== 'forward') {
      throw new Error(`BRIDGE ${bridge.id}: direction は 'forward' 必須（逆止弁制約）`);
    }
    if (bridge.level <= prevLevel) {
      throw new Error(`BRIDGE ${bridge.id}: level は strictly increasing 必須（循環なし制約）`);
    }
    prevLevel = bridge.level;

    const weight = bridge.weight_vector_fn(ctx);
    // 各BRIDGEは「全次元に同一の重みを掛ける」一様重みベクトルを返す契約になっている
    // （水路のゲートは次元ごとに開閉するのではなく、ライン全体を一括で通す/止める/減衰させる）。
    // weight[0]を代表値として扱う前に、その前提が本当に成り立っているかを検証する。
    // 破られていれば実行時の異常値ではなくモデル定義そのものの誤りなので、
    // 空/false/0への暗黙変換はせず、原因のBRIDGE IDを含めて明示的にエラーとする。
    const isUniformWeight = weight.every(x => x === weight[0]);
    if (!isUniformWeight) {
      throw new Error(`BRIDGE ${bridge.id}: weight_vector_fn は一様重みベクトル必須（MODEL_DEFINITION_ERROR）。次元ごとに異なる重みが返された: [${weight.join(', ')}]`);
    }
    flow = hadamard(flow, weight);
    const w = weight[0]; // 一様重みベクトルの代表値（検証済み）
    const blocked = isZeroVector(flow);

    trace.push({
      bridgeId: bridge.id,
      level: bridge.level,
      weight: w,
      passed: !blocked,
      attenuated: !blocked && w < 1,
    });

    if (blocked) {
      return {
        flow,
        blocked: true,
        blockedAt: bridge.id,
        blockReason: bridge.reason_fn ? bridge.reason_fn(ctx) : null,
        trace,
      };
    }
  }

  return { flow, blocked: false, blockedAt: null, blockReason: null, trace };
}
