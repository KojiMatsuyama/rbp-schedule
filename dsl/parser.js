// dsl/parser.js — 境界言語DSLの再帰下降パーサ
// 境界言語BNF.txt が定義する5つの<Boundary>のうち、
// 行列導出に必要な Domain-Declaration / Demand-Boundary / Bridge-Boundary /
// SpecBridge-Boundary のみを構造化されたASTへ解析する。
// Reflect-Boundary / Spec-Boundary は、Weight-Rule/Condition/Score-Expr等の
// 複雑な式文法を実装せずに済むよう、トークン列上の中括弧平衡カウントで
// ブロック全体を読み飛ばす（本コンパイラは2つの行列導出にのみ関心がある）。
const { tokenize } = require('./lexer');

class Parser {
  constructor(tokens) {
    this.tokens = tokens;
    this.pos = 0;
  }

  peek(offset) {
    return this.tokens[this.pos + (offset || 0)];
  }

  next() {
    const t = this.tokens[this.pos];
    this.pos++;
    return t;
  }

  isIdent(value) {
    const t = this.peek();
    return t.type === 'IDENT' && t.value === value;
  }

  isPunct(value) {
    const t = this.peek();
    return t.type === 'PUNCT' && t.value === value;
  }

  error(message) {
    const t = this.peek();
    throw new Error(`Parse error (line ${t.line}): ${message}（実際のトークン: ${t.type} "${t.value}"）`);
  }

  expectIdent(value) {
    const t = this.next();
    if (t.type !== 'IDENT' || t.value !== value) {
      throw new Error(`Parse error (line ${t.line}): "${value}" を期待しましたが "${t.value}" でした`);
    }
    return t;
  }

  expectPunct(value) {
    const t = this.next();
    if (t.type !== 'PUNCT' || t.value !== value) {
      throw new Error(`Parse error (line ${t.line}): "${value}" を期待しましたが "${t.value}" でした`);
    }
    return t;
  }

  expectType(type) {
    const t = this.next();
    if (t.type !== type) {
      throw new Error(`Parse error (line ${t.line}): ${type}を期待しましたが ${t.type} "${t.value}" でした`);
    }
    return t;
  }

  // 次のトークンが"{"である前提で、対応する"}"までをトークン単位で読み飛ばす。
  // 文字列リテラルとコメントはレクサ段階で単一トークン化されているため、
  // 説明文中に"{"/"}"が現れても深さカウントを乱さない。
  skipBalancedBlock() {
    this.expectPunct('{');
    let depth = 1;
    while (depth > 0) {
      const t = this.next();
      if (t.type === 'EOF') {
        throw new Error('Parse error: ブロックが閉じられる前にファイル末尾に達しました（"{"と"}"の対応が取れていません）');
      }
      if (t.type === 'PUNCT' && t.value === '{') depth++;
      if (t.type === 'PUNCT' && t.value === '}') depth--;
    }
  }
}

const LINE_REF_RE = /^Line-(\d+)$/;
const BOX_ID_RE = /^BOX-(.+)$/;

function parseLineRefSet(p) {
  const refs = [];
  const first = p.expectType('IDENT');
  const m0 = LINE_REF_RE.exec(first.value);
  if (!m0) throw new Error(`Parse error (line ${first.line}): "Line-N"形式を期待しましたが "${first.value}" でした`);
  refs.push({ index: Number(m0[1]), line: first.line });
  while (p.isPunct(',')) {
    p.next();
    const t = p.expectType('IDENT');
    const m = LINE_REF_RE.exec(t.value);
    if (!m) throw new Error(`Parse error (line ${t.line}): "Line-N"形式を期待しましたが "${t.value}" でした`);
    refs.push({ index: Number(m[1]), line: t.line });
  }
  return refs;
}

function parseDomainDeclaration(p) {
  p.expectIdent('Domain');
  const id = p.expectType('IDENT').value;
  p.expectPunct('{');

  let dimensions = null;
  let lines = null;

  while (!p.isPunct('}')) {
    if (p.isIdent('dimensions')) {
      p.next();
      p.expectPunct(':');
      dimensions = p.expectType('NUMBER').value;
      p.expectPunct(';');
    } else if (p.isIdent('lines')) {
      p.next();
      p.expectPunct(':');
      p.expectPunct('[');
      lines = [p.expectType('STRING').value];
      while (p.isPunct(',')) {
        p.next();
        lines.push(p.expectType('STRING').value);
      }
      p.expectPunct(']');
      p.expectPunct(';');
    } else {
      p.error('Domain宣言内では "dimensions" または "lines" を期待しました');
    }
  }
  p.expectPunct('}');

  if (dimensions === null) throw new Error(`Parse error: Domain ${id} に dimensions が定義されていません`);
  if (lines === null) throw new Error(`Parse error: Domain ${id} に lines が定義されていません`);

  return { id, dimensions, lines };
}

function parseLineDef(p) {
  const nameTok = p.expectType('IDENT');
  const m = LINE_REF_RE.exec(nameTok.value);
  if (!m) throw new Error(`Parse error (line ${nameTok.line}): "Line-N"形式を期待しました`);
  const index = Number(m[1]);
  p.expectPunct(':');
  const name = p.expectType('IDENT').value;
  p.expectPunct('(');
  const kindTok = p.expectType('IDENT');
  if (kindTok.value !== 'disease' && kindTok.value !== 'pest') {
    throw new Error(`Parse error (line ${kindTok.line}): Line-Defの種別は disease または pest である必要があります（実際: "${kindTok.value}"）`);
  }
  p.expectPunct(')');
  p.expectPunct(';');
  return { index, name, kind: kindTok.value, line: nameTok.line };
}

function parseDemandBoundary(p) {
  p.expectIdent('Demand-Boundary');
  const id = p.expectType('IDENT').value;
  p.expectPunct('{');

  const lineDefs = [];
  while (!p.isPunct('}')) {
    if (p.peek().type === 'IDENT' && LINE_REF_RE.test(p.peek().value)) {
      lineDefs.push(parseLineDef(p));
    } else if (p.isIdent('Demand-Rule')) {
      p.next();
      p.skipBalancedBlock();
    } else {
      p.error('Demand-Boundary内では Line-Def または Demand-Rule を期待しました');
    }
  }
  p.expectPunct('}');
  return { id, lineDefs };
}

function parseAsName(p) {
  if (p.isIdent('as')) {
    p.next();
    return p.expectType('STRING').value;
  }
  return null;
}

function parseBoxDef(p) {
  const idTok = p.expectType('IDENT');
  const m = BOX_ID_RE.exec(idTok.value);
  if (!m) throw new Error(`Parse error (line ${idTok.line}): "BOX-<id>"形式を期待しました`);
  p.expectPunct(':');
  p.expectPunct('{');
  const rawRefs = p.isPunct('}') ? [] : parseLineRefSet(p);
  p.expectPunct('}');
  const asName = parseAsName(p);
  p.expectPunct(';');
  return {
    id: m[1],
    lineRefs: rawRefs.map(r => r.index),
    asName,
    line: idTok.line,
    _rawRefs: rawRefs,
  };
}

function parseExtensionPolicyValue(p) {
  const t = p.next();
  if (t.type !== 'IDENT') throw new Error(`Parse error (line ${t.line}): 識別子を期待しました`);
  return t.value;
}

function parseBridgeExtensionPolicy(p) {
  p.expectIdent('Bridge-Extension-Policy');
  p.expectPunct('{');
  const policy = { onUndefined: null, namingRule: null, idRule: null };
  while (!p.isPunct('}')) {
    if (p.isIdent('on-UNDEFINED')) {
      p.next();
      p.expectPunct('=>');
      policy.onUndefined = parseExtensionPolicyValue(p);
      p.expectPunct(';');
    } else if (p.isIdent('naming-rule')) {
      p.next();
      p.expectPunct('=');
      policy.namingRule = p.expectType('STRING').value;
      p.expectPunct(';');
    } else if (p.isIdent('id-rule')) {
      p.next();
      p.expectPunct('=');
      policy.idRule = parseExtensionPolicyValue(p);
      p.expectPunct(';');
    } else {
      p.error('Bridge-Extension-Policy内では on-UNDEFINED / naming-rule / id-rule を期待しました');
    }
  }
  p.expectPunct('}');
  return policy;
}

function parseBridgeBoundary(p) {
  p.expectIdent('Bridge-Boundary');
  const id = p.expectType('IDENT').value;
  p.expectPunct('{');

  const boxDefs = [];
  let extensionPolicy = { onUndefined: null, namingRule: null, idRule: null };

  while (!p.isPunct('}')) {
    if (p.peek().type === 'IDENT' && BOX_ID_RE.test(p.peek().value)) {
      boxDefs.push(parseBoxDef(p));
    } else if (p.isIdent('Bridge-Rule')) {
      p.next();
      p.skipBalancedBlock();
    } else if (p.isIdent('Bridge-Extension-Policy')) {
      extensionPolicy = parseBridgeExtensionPolicy(p);
    } else {
      p.error('Bridge-Boundary内では BOX-Def / Bridge-Rule / Bridge-Extension-Policy を期待しました');
    }
  }
  p.expectPunct('}');
  return { id, boxDefs, extensionPolicy };
}

function parseAttributeValue(p) {
  const t = p.peek();
  if (t.type === 'STRING') {
    p.next();
    return t.value;
  }
  if (t.type === 'NUMBER') {
    p.next();
    return t.value;
  }
  if (t.type === 'IDENT' && (t.value === 'true' || t.value === 'false')) {
    p.next();
    return t.value === 'true';
  }
  if (t.type === 'PUNCT' && t.value === '[') {
    p.next();
    const values = [];
    if (!p.isPunct(']')) {
      values.push(parseAttributeValue(p));
      while (p.isPunct(',')) {
        p.next();
        values.push(parseAttributeValue(p));
      }
    }
    p.expectPunct(']');
    return values;
  }
  throw new Error(`Parse error (line ${t.line}): 属性値（文字列・数値・真偽値・配列）を期待しましたが "${t.value}" でした`);
}

function parseCandidateDef(p) {
  p.expectIdent('Candidate');
  const idTok = p.expectType('IDENT');
  p.expectPunct(':');
  p.expectPunct('{');
  p.expectIdent('targets');
  p.expectPunct('=');
  const rawTargets = parseLineRefSet(p);
  p.expectPunct(';');

  const attributes = [];
  while (!p.isPunct('}')) {
    const keyTok = p.expectType('IDENT');
    p.expectPunct('=');
    const value = parseAttributeValue(p);
    p.expectPunct(';');
    attributes.push({ key: keyTok.value, value });
  }
  p.expectPunct('}');
  p.expectPunct(';');

  return {
    id: idTok.value,
    targets: rawTargets.map(r => r.index),
    attributes,
    line: idTok.line,
    _rawTargets: rawTargets,
  };
}

function parseSpecBridgeBoundary(p) {
  p.expectIdent('SpecBridge-Boundary');
  const id = p.expectType('IDENT').value;
  p.expectPunct('{');

  const candidateDefs = [];
  let extensionPolicy = { onUndefined: null, namingRule: null, idRule: null };

  while (!p.isPunct('}')) {
    if (p.isIdent('Candidate')) {
      candidateDefs.push(parseCandidateDef(p));
    } else if (p.isIdent('SpecBridge-Rule')) {
      p.next();
      p.skipBalancedBlock();
    } else if (p.isIdent('Bridge-Extension-Policy')) {
      extensionPolicy = parseBridgeExtensionPolicy(p);
    } else {
      p.error('SpecBridge-Boundary内では Candidate / SpecBridge-Rule / Bridge-Extension-Policy を期待しました');
    }
  }
  p.expectPunct('}');
  return { id, candidateDefs, extensionPolicy };
}

// Reflect-Boundary / Spec-Boundary はキーワード＋識別子だけ消費し、
// 本体はskipBalancedBlockで丸ごと読み飛ばす（本コンパイラの対象外）。
function skipNamedBoundary(p, keyword) {
  p.expectIdent(keyword);
  p.expectType('IDENT');
  p.skipBalancedBlock();
}

function parseProgram(source) {
  const tokens = tokenize(source);
  const p = new Parser(tokens);

  const domain = parseDomainDeclaration(p);
  const demand = parseDemandBoundary(p);
  const bridge = parseBridgeBoundary(p);
  const specBridge = parseSpecBridgeBoundary(p);
  skipNamedBoundary(p, 'Reflect-Boundary');
  skipNamedBoundary(p, 'Spec-Boundary');

  if (p.peek().type !== 'EOF') {
    p.error('プログラム終端の後に余分なトークンがあります');
  }

  return { domain, demand, bridge, specBridge };
}

module.exports = { parseProgram, Parser, tokenize };
