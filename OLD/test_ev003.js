const { evaluate } = require('./DemandRBP');
const { EVAL_BOX_DATASET } = require('./21.EVAL_BOX_DATASET');
const { RBP_EVAL_BOXES } = require('./RBP_EVAL_BOXES');

function cosineSimilarity(a, b) {
  let dot = 0, magA = 0, magB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    magA += a[i] * a[i];
    magB += b[i] * b[i];
  }
  magA = Math.sqrt(magA);
  magB = Math.sqrt(magB);
  if (magA === 0 || magB === 0) return 0;
  return dot / (magA * magB);
}

const ev003 = EVAL_BOX_DATASET.find(e => e.ENTRY_ID === 'EV-003');
console.log('EV-003 ベクトル:', ev003.vector);
console.log('');

const result = evaluate(ev003.vector);
console.log('最終LINE:', result.final_line);
console.log('最終ベクトル:', result.final_vector);
console.log('');

console.log('各EvalBoxとのコサイン類似度:');
RBP_EVAL_BOXES.forEach(box => {
  const score = cosineSimilarity(result.final_vector, box.eval_vector);
  console.log(`  ${box.id}: ${score.toFixed(4)}`);
});

console.log('');
console.log('予測:', result.evaluated_box.id);
console.log('実際:', ev003.best_eb.label);
