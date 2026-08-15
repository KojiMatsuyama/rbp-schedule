// dsl/errors.js — DSLコンパイラのエラー型
// 境界言語BNF.txt §8 の静的制約（S1〜S3・重複ID）違反、および構文エラーを表す。
// framework/mirror.js の MODEL_DEFINITION_ERROR 命名にならい、
// 「原因のID・違反した制約」を名指しする明示的なエラーとする（暗黙の補正はしない）。

class DslError extends Error {
  constructor(code, message, line) {
    super(`MODEL_DEFINITION_ERROR[${code}] (line ${line}): ${message}`);
    this.code = code;
    this.line = line;
  }
}

// validate()が収集した全違反をまとめて保持する。
// コンパイル時の一括診断のため、最初の1件で止めず全件を報告する。
class DslValidationError extends Error {
  constructor(errors) {
    super(`DSL検証エラー: ${errors.length}件の違反`);
    this.errors = errors;
  }
}

module.exports = { DslError, DslValidationError };
