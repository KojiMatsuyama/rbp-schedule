// tests/test_dsl_validate.js — dsl/validate.js の静的制約チェックを検証。
// dsl/samples/invalid_*.txt の各ファイルが、境界言語BNF.txt §8のS1〜S3・
// 重複IDのうち意図した1種類だけを検出することを確認する。
const fs = require('fs');
const path = require('path');
const { parseProgram } = require('../dsl/parser');
const { validate } = require('../dsl/validate');
const { deriveAll } = require('../dsl/derive_matrices');
const { DslValidationError } = require('../dsl/errors');
const { describe, it, assert, summary } = require('./tiny_test');

const SAMPLES_DIR = path.join(__dirname, '..', 'dsl', 'samples');

function loadAst(filename) {
  const source = fs.readFileSync(path.join(SAMPLES_DIR, filename), 'utf8');
  return parseProgram(source);
}

describe('validate（正例）', () => {
  it('full_program.txtは違反0件', () => {
    const ast = loadAst('full_program.txt');
    const errors = validate(ast);
    assert.strictEqual(errors.length, 0);
  });
});

describe('validate（異常系・各ファイル1種類の違反のみ検出）', () => {
  it('invalid_dimension_mismatch.txt → S1_DIMENSION_MISMATCHのみ', () => {
    const ast = loadAst('invalid_dimension_mismatch.txt');
    const errors = validate(ast);
    assert.strictEqual(errors.length, 1);
    assert.strictEqual(errors[0].code, 'S1_DIMENSION_MISMATCH');
  });

  it('invalid_line_ref_out_of_range.txt → S2_LINE_REF_OUT_OF_RANGEのみ', () => {
    const ast = loadAst('invalid_line_ref_out_of_range.txt');
    const errors = validate(ast);
    assert.strictEqual(errors.length, 1);
    assert.strictEqual(errors[0].code, 'S2_LINE_REF_OUT_OF_RANGE');
  });

  it('invalid_duplicate_box_id.txt → DUPLICATE_BOX_IDのみ', () => {
    const ast = loadAst('invalid_duplicate_box_id.txt');
    const errors = validate(ast);
    assert.strictEqual(errors.length, 1);
    assert.strictEqual(errors[0].code, 'DUPLICATE_BOX_ID');
  });

  it('invalid_duplicate_candidate_id.txt → DUPLICATE_CANDIDATE_IDのみ', () => {
    const ast = loadAst('invalid_duplicate_candidate_id.txt');
    const errors = validate(ast);
    assert.strictEqual(errors.length, 1);
    assert.strictEqual(errors[0].code, 'DUPLICATE_CANDIDATE_ID');
  });
});

describe('deriveAll（違反があれば導出前にDslValidationErrorを投げる）', () => {
  it('違反のあるASTからは行列を導出しない', () => {
    const ast = loadAst('invalid_dimension_mismatch.txt');
    assert.throws(() => deriveAll(ast), DslValidationError);
  });

  it('DslValidationError.errorsに違反の詳細が入っている', () => {
    const ast = loadAst('invalid_line_ref_out_of_range.txt');
    try {
      deriveAll(ast);
      assert.fail('DslValidationErrorが投げられるはず');
    } catch (err) {
      assert.ok(err instanceof DslValidationError);
      assert.strictEqual(err.errors.length, 1);
      assert.strictEqual(err.errors[0].code, 'S2_LINE_REF_OUT_OF_RANGE');
    }
  });

  it('違反のないASTからは正常に行列を導出する', () => {
    const ast = loadAst('full_program.txt');
    const derived = deriveAll(ast);
    assert.ok(derived.bridgeMatrix);
    assert.ok(derived.specBridgeMatrix);
  });
});

summary();
