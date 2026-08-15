// dsl/codegen.js — 導出済み行列データから、data/eval_boxes.js・data/pesticides.js と
// 同形式のJSソース文字列を生成する（純粋な文字列生成のみ。ファイルI/Oは行わない）。
// 定数名（EB_VECTORS/EB_MATRIX/EB_NAMES, PESTICIDE_DB/TARGET_MATRIX）を実ファイルと揃え、
// <script>読み込み前提の非モジュールグローバルスクリプトという既存の形式に合わせる
// （data/eval_boxes.js・data/pesticides.jsとdiffして意味のある比較ができるようにするため）。

function jsStringLiteral(s) {
  return JSON.stringify(s);
}

function jsAttributeValue(v) {
  if (Array.isArray(v)) return `[${v.map(jsAttributeValue).join(',')}]`;
  if (typeof v === 'string') return jsStringLiteral(v);
  return String(v);
}

function genEvalBoxesJs(bridgeMatrix, sourcePath) {
  const { vectors, names } = bridgeMatrix;
  const lines = [];
  lines.push('// dsl/output/eval_boxes.generated.js — DSLコンパイラ生成（手編集しないこと）');
  lines.push(`// 入力: ${sourcePath}`);
  lines.push('');
  lines.push('const EB_VECTORS = {');
  for (const key of Object.keys(vectors)) {
    lines.push(`  ${jsStringLiteral(key)}: [${vectors[key].join(',')}],`);
  }
  lines.push('};');
  lines.push('');
  lines.push('const EB_MATRIX = Object.values(EB_VECTORS);');
  lines.push('');
  lines.push('const EB_NAMES = {');
  for (const key of Object.keys(names)) {
    lines.push(`  ${jsStringLiteral(key)}: ${jsStringLiteral(names[key])},`);
  }
  lines.push('};');
  lines.push('');
  return lines.join('\n');
}

function genPesticidesJs(specBridgeMatrix, sourcePath) {
  const { candidates } = specBridgeMatrix;
  const lines = [];
  lines.push('// dsl/output/pesticides.generated.js — DSLコンパイラ生成（手編集しないこと）');
  lines.push(`// 入力: ${sourcePath}`);
  lines.push('');
  lines.push('const PESTICIDE_DB = [');
  for (const c of candidates) {
    const fields = [`id:${jsStringLiteral(c.id)}`, `targetVector:[${c.targetVector.join(',')}]`];
    for (const key of Object.keys(c)) {
      if (key === 'id' || key === 'targetVector') continue;
      fields.push(`${key}:${jsAttributeValue(c[key])}`);
    }
    lines.push(`  { ${fields.join(', ')} },`);
  }
  lines.push('];');
  lines.push('');
  lines.push('const TARGET_MATRIX = PESTICIDE_DB.map(p => p.targetVector);');
  lines.push('');
  return lines.join('\n');
}

module.exports = { genEvalBoxesJs, genPesticidesJs };
