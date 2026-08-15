const { evaluate, bridgeConditionMet } = require('./DemandRBP');
const { RBP_BRIDGES } = require('./RBP_BRIDGES');
const { EVAL_BOX_DATASET } = require('./21.EVAL_BOX_DATASET');

const ev060 = EVAL_BOX_DATASET.find(e => e.ENTRY_ID === 'EV-060');
console.log('EV-060 ベクトル:', ev060.vector);
console.log('expected: EB-11');
console.log('');

// ブリッジ確認
const l25 = RBP_BRIDGES.find(b => b.id === 'BRIDGE-L2-5');
console.log('L2.5条件:', l25.description);
console.log('  threshold:', l25.threshold_vector);
console.log('  met?', bridgeConditionMet(ev060.vector, l25));
console.log('');

const result = evaluate(ev060.vector);
console.log('結果: LINE=' + result.final_line + ', BOX=' + result.evaluated_box.id);
console.log('最終ベクトル:', result.final_vector);
