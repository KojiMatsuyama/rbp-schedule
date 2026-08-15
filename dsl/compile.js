#!/usr/bin/env node
// dsl/compile.js — 境界言語DSLソースを読み、
// 要求評価RBP行列（BridgeMatrix）と仕様決定RBP行列（SpecBridgeMatrix）を導出してJSを書き出すCLI。
// 使い方: node dsl/compile.js <input.txt> [outDir]
// outDir省略時は dsl/output/ 配下に eval_boxes.generated.js / pesticides.generated.js を書く。
const fs = require('fs');
const path = require('path');
const { parseProgram } = require('./parser');
const { deriveAll } = require('./derive_matrices');
const { genEvalBoxesJs, genPesticidesJs } = require('./codegen');
const { DslValidationError } = require('./errors');

function compile(source, sourceLabel) {
  const ast = parseProgram(source);
  return deriveAll(ast);
}

function main(argv) {
  const inputPath = argv[2];
  if (!inputPath) {
    console.error('使い方: node dsl/compile.js <input.txt> [outDir]');
    process.exitCode = 1;
    return;
  }
  const outDir = argv[3] || path.join(__dirname, 'output');
  const source = fs.readFileSync(inputPath, 'utf8');

  let derived;
  try {
    derived = compile(source, inputPath);
  } catch (err) {
    if (err instanceof DslValidationError) {
      console.error(`${err.errors.length}件の検証エラー:`);
      for (const e of err.errors) console.error(`  ${e.message}`);
    } else {
      console.error(`コンパイルエラー: ${err.message}`);
    }
    process.exitCode = 1;
    return;
  }

  fs.mkdirSync(outDir, { recursive: true });
  const evalBoxesPath = path.join(outDir, 'eval_boxes.generated.js');
  const pesticidesPath = path.join(outDir, 'pesticides.generated.js');

  fs.writeFileSync(evalBoxesPath, genEvalBoxesJs(derived.bridgeMatrix, inputPath));
  fs.writeFileSync(pesticidesPath, genPesticidesJs(derived.specBridgeMatrix, inputPath));

  console.log(`要求評価RBP行列（BridgeMatrix）: ${Object.keys(derived.bridgeMatrix.vectors).length}件のEVAL_BOXを ${evalBoxesPath} に書き出しました`);
  console.log(`仕様決定RBP行列（SpecBridgeMatrix）: ${derived.specBridgeMatrix.candidates.length}件の候補を ${pesticidesPath} に書き出しました`);
}

if (require.main === module) {
  main(process.argv);
}

module.exports = { compile, main };
