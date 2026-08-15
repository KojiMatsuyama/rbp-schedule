// tests/test_dsl_codegen.js — dsl/codegen.js が生成するJSソース文字列を検証。
// 生成物はdata/eval_boxes.js等と同じ非モジュールグローバルスクリプト形式のため、
// tests/load_app.jsと同じ手法（vmで一時ファイルを再ロード）で読み戻し、
// コード生成前のin-memory構造と一致するかを確認する
// （カンマ・クォート欠落等のフォーマットバグを検出する）。
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const { parseProgram } = require('../dsl/parser');
const { deriveAll } = require('../dsl/derive_matrices');
const { genEvalBoxesJs, genPesticidesJs } = require('../dsl/codegen');
const { describe, it, assert, assertDeepEqualAcrossRealms, summary } = require('./tiny_test');

const source = fs.readFileSync(path.join(__dirname, '..', 'dsl', 'samples', 'full_program.txt'), 'utf8');
const derived = deriveAll(parseProgram(source));

function loadGeneratedInVm(code) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsl-codegen-test-'));
  const filePath = path.join(dir, 'generated.js');
  fs.writeFileSync(filePath, code);
  const ctx = { console, Object, Array, JSON };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(filePath, 'utf8'), ctx, { filename: filePath });
  return ctx;
}

describe('genEvalBoxesJs（往復テスト）', () => {
  const code = genEvalBoxesJs(derived.bridgeMatrix, 'dsl/samples/full_program.txt');
  const ctx = loadGeneratedInVm(code);

  it('生成コードはEB_VECTORSを持ち、導出結果と一致する', () => {
    const reloaded = vm.runInContext('EB_VECTORS', ctx);
    assertDeepEqualAcrossRealms(reloaded, derived.bridgeMatrix.vectors);
  });

  it('生成コードのEB_MATRIXはObject.values(EB_VECTORS)である', () => {
    const reloadedMatrix = vm.runInContext('EB_MATRIX', ctx);
    const reloadedVectors = vm.runInContext('EB_VECTORS', ctx);
    assertDeepEqualAcrossRealms(reloadedMatrix, Object.values(reloadedVectors));
  });

  it('生成コードのEB_NAMESは導出結果と一致する', () => {
    const reloaded = vm.runInContext('EB_NAMES', ctx);
    assertDeepEqualAcrossRealms(reloaded, derived.bridgeMatrix.names);
  });
});

describe('genPesticidesJs（往復テスト）', () => {
  const code = genPesticidesJs(derived.specBridgeMatrix, 'dsl/samples/full_program.txt');
  const ctx = loadGeneratedInVm(code);

  it('生成コードのPESTICIDE_DBは導出結果と一致する', () => {
    const reloaded = vm.runInContext('PESTICIDE_DB', ctx);
    assertDeepEqualAcrossRealms(reloaded, derived.specBridgeMatrix.candidates);
  });

  it('生成コードのTARGET_MATRIXはPESTICIDE_DB.map(p=>p.targetVector)である', () => {
    const reloadedMatrix = vm.runInContext('TARGET_MATRIX', ctx);
    assertDeepEqualAcrossRealms(reloadedMatrix, derived.specBridgeMatrix.matrix);
  });
});

summary();
