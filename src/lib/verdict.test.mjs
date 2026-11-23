/**
 * `verdict.js` 的断言机 —— 直接跑：`node src/lib/verdict.test.mjs`
 *
 * 零依赖（只用 `node:assert`），因为被测对象是**最后一道闸**：
 * 它要是依赖一堆东西才能跑，谁也不会在改完裁决逻辑后真去跑它。
 *
 * 覆盖（SPEC §7·3 要求的最低四类 + 本仓库补的兼容面）：
 *   1. 正常三态
 *   2. 缺字段 / 无结构化裁决
 *   3. pass 但 blocking 非空（自相矛盾）
 *   4. evidence 为空（该条降 E0 + 追加「证据缺失」）
 *   5. 只认结构化字段（散文里的 "pass" 不算）
 *   6. 历史真实裁决（`docs/decisions/*.json`）能读 —— 存在才跑
 *
 * 退出码：0 = 全绿；1 = 有失败。
 */
import { strict as assert } from 'node:assert';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { EVIDENCE_TYPES, NO_VERDICT_BLOCKING, VERDICTS, assertVerdict, normalizeVerdict, parseVerdict } from './verdict.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..', '..');

let passed = 0;
/** @type {{ name: string, message: string }[]} */
const failures = [];

/** 极简用例跑法：抛错即失败，不中断其余用例。 */
function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok   ${name}`);
  } catch (error) {
    failures.push({ name, message: error?.message ?? String(error) });
    console.log(`  FAIL ${name}`);
    console.log(`       ${String(error?.message ?? error).split('\n').join('\n       ')}`);
  }
}

/** 包一层「解析 + 归一化」，多数用例只关心这一步的结果。 */
function run(text) {
  return normalizeVerdict(parseVerdict(text));
}

/** 拼一个合规的裁决 JSON 文本。 */
function json(value, fence = true) {
  const body = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return fence ? '先说两句闲话。\n\n```json\n' + body + '\n```\n\n以上就是我的结论。' : body;
}

/** 一条证据齐备的 finding。 */
function finding(overrides = {}) {
  return { id: 'C1', claim: '某条验收标准', evidence: 'src/index.js:12 `export function apply`', type: 'E2', ...overrides };
}

// ── 1. 正常三态 ────────────────────────────────────────────────────────────────
console.log('\n[1] 正常三态');

test('pass：三态之一，confidence 保留，blocking 为空', () => {
  const v = run(json({ verdict: 'pass', confidence: 0.91, blocking: [], findings: [finding()] }));
  assert.equal(v.verdict, 'pass');
  assert.equal(v.confidence, 0.91);
  assert.deepEqual(v.blocking, []);
  assert.equal(v.findings.length, 1);
  assert.equal(v.findings[0].type, 'E2');
  assert.doesNotThrow(() => assertVerdict(v));
});

test('rework：带 blocking 原样保留，且能通过 final gate', () => {
  const v = run(
    json({
      verdict: 'rework',
      confidence: 0.8,
      blocking: [{ id: 'B1', what: '缺证据', why: '契约要求', evidence: 'docs/REVIEW-CONTRACT.md:49' }],
      findings: [finding({ type: 'E1' })],
    }),
  );
  assert.equal(v.verdict, 'rework');
  assert.equal(v.blocking.length, 1);
  assert.match(v.blocking[0], /\[B1\] 缺证据/);
  assert.match(v.blocking[0], /证据：docs\/REVIEW-CONTRACT\.md:49/);
  assert.doesNotThrow(() => assertVerdict(v));
});

test('escalate：三态之一，不被改写成 pass', () => {
  const v = run(json({ verdict: 'escalate', confidence: 0.3, blocking: [], findings: [] }));
  assert.equal(v.verdict, 'escalate');
  assert.deepEqual(v.blocking, []);
  assert.ok(VERDICTS.includes(v.verdict));
});

// ── 2. 缺字段 / 无结构化裁决 ────────────────────────────────────────────────────
console.log('\n[2] 缺字段与解析失败');

test('整段没有 JSON → escalate（不是 fail，更不是 pass）', () => {
  const v = run('我看了一遍，基本没问题，可以过。');
  assert.equal(v.verdict, 'escalate');
  assert.equal(v.confidence, 0);
  assert.deepEqual(v.findings, []);
  assert.equal(v.blocking.length, 1);
  assert.equal(v.blocking[0], NO_VERDICT_BLOCKING);
  assert.ok(v.notes.includes('no-structured-verdict'));
});

test('JSON 里没有 verdict 键 → escalate', () => {
  const v = run(json({ confidence: 0.9, blocking: [], findings: [] }));
  assert.equal(v.verdict, 'escalate');
  assert.equal(v.blocking[0], NO_VERDICT_BLOCKING);
});

test('verdict 非法值 → escalate（不猜它想说什么）', () => {
  const v = run(json({ verdict: 'ok', confidence: 0.9, blocking: [], findings: [] }));
  assert.equal(v.verdict, 'escalate');
  assert.ok(v.notes.some((note) => note.includes('verdict 非法')));
  assert.doesNotThrow(() => assertVerdict(v));
});

test('空字符串 / 非字符串输入 → escalate', () => {
  for (const input of ['', '   ', null, undefined, 42, {}]) {
    const v = normalizeVerdict(parseVerdict(input));
    assert.equal(v.verdict, 'escalate', `输入 ${JSON.stringify(input)} 应降级 escalate`);
  }
});

test('findings 缺失或不是数组 → 空数组，不崩', () => {
  assert.deepEqual(run(json({ verdict: 'pass', confidence: 1, blocking: [] })).findings, []);
  assert.deepEqual(run(json({ verdict: 'pass', confidence: 1, blocking: [], findings: 7 })).findings, []);
});

test('坏 JSON（引号未转义）→ 无法解析 → escalate', () => {
  const broken = '```json\n{ "verdict": "pass", "confidence": 0.9, "note": "他说"行"了" }\n```';
  const v = run(broken);
  assert.equal(v.verdict, 'escalate');
  assert.equal(v.blocking[0], NO_VERDICT_BLOCKING);
});

// ── 3. pass + blocking 自相矛盾 ────────────────────────────────────────────────
console.log('\n[3] pass 但 blocking 非空');

test('pass 且 blocking 非空 → 强制 rework', () => {
  const v = run(
    json({
      verdict: 'pass',
      confidence: 0.77,
      blocking: ['[B1] 有一个真问题 ｜ 证据：a.js:3'],
      findings: [finding()],
    }),
  );
  assert.equal(v.verdict, 'rework');
  assert.ok(v.notes.some((note) => note.includes('pass 但 blocking')));
  assert.doesNotThrow(() => assertVerdict(v));
});

test('pass + blocking 对象数组（历史形状）同样降级', () => {
  const v = run(json({ verdict: 'pass', confidence: 0.6, blocking: [{ id: 'B2', what: '引用对不上' }] }));
  assert.equal(v.verdict, 'rework');
});

// ── 4. 证据为空 ────────────────────────────────────────────────────────────────
console.log('\n[4] 证据为空');

test('evidence 为空 → 该条降 E0，并追加一条「证据缺失」blocking', () => {
  const v = run(
    json({
      verdict: 'rework',
      confidence: 0.5,
      blocking: [],
      findings: [finding({ id: 'C2', claim: '性能没问题', evidence: '', type: 'E3' })],
    }),
  );
  assert.equal(v.findings[0].type, 'E0', '自报 E3 但没证据 → E0');
  assert.equal(v.findings[0].evidence, '');
  const missing = v.blocking.filter((item) => item.startsWith('证据缺失：'));
  assert.equal(missing.length, 1, 'blocking 里应当有且只有一条证据缺失');
  assert.match(missing[0], /\[C2\]/);
  assert.match(missing[0], /E0/);
});

test('pass + 证据为空 → 变成 rework（不允许无证据通过）', () => {
  const v = run(json({ verdict: 'pass', confidence: 0.99, blocking: [], findings: [finding({ evidence: '' })] }));
  assert.equal(v.verdict, 'rework', '没有证据的 pass 必须被拦下');
  assert.ok(v.blocking.some((item) => item.startsWith('证据缺失：')));
});

test('历史形状：claimed_strength 声明 E2 但证据为空 → E0', () => {
  const v = run(
    json({
      verdict: 'rework',
      confidence: 0.4,
      blocking: [],
      criteria: [{ id: 'C1', type: 'fact-consistent', status: 'pass', claimed_strength: 'E2', evidence: '' }],
    }),
  );
  assert.equal(v.findings[0].type, 'E0');
});

test('每条 finding 的 type 都在 E0–E3 内', () => {
  const v = run(
    json({
      verdict: 'rework',
      confidence: 0.5,
      blocking: [],
      findings: [finding({ id: 'A', type: 'E2' }), finding({ id: 'B', type: 'bogus', evidence: 'x:1' }), finding({ id: 'C', evidence: '' })],
    }),
  );
  for (const f of v.findings) assert.ok(EVIDENCE_TYPES.includes(f.type), `${f.id} 的 type=${f.type} 不在 E0–E3`);
  // 非法 type 且无 claimed_strength → 按 E0（「没声明强度 = 按 E0 处理」）
  assert.equal(v.findings[1].type, 'E0');
});

// ── 5. 只认结构化字段 ──────────────────────────────────────────────────────────
console.log('\n[5] 只认结构化字段');

test('散文里出现 pass 字样不算裁决', () => {
  const v = run('结论：pass。以上。\n\n其实我建议 pass，因为我看过代码了。');
  assert.equal(v.verdict, 'escalate');
});

test('多个 JSON 块时取最后一个（模型先复述、后给结论）', () => {
  const text = [
    '草稿：',
    '```json',
    JSON.stringify({ verdict: 'rework', confidence: 0.2, blocking: ['草稿'], findings: [] }),
    '```',
    '修正后：',
    '```json',
    JSON.stringify({ verdict: 'pass', confidence: 0.95, blocking: [], findings: [finding()] }),
    '```',
  ].join('\n');
  const v = run(text);
  assert.equal(v.verdict, 'pass');
  assert.equal(v.confidence, 0.95);
});

test('evidence 里含花括号与转义引号也能正确配平', () => {
  const messy =
    '前言 ' +
    JSON.stringify({
      verdict: 'pass',
      confidence: 0.5,
      blocking: [],
      findings: [{ id: 'C1', claim: '主张里有 { 和 }', evidence: 'x.js:2 -> {"a":1}', type: 'E2' }],
    }) +
    ' 后记';
  const v = run(messy);
  assert.equal(v.verdict, 'pass');
  assert.equal(v.findings[0].evidence, 'x.js:2 -> {"a":1}');
});

// ── 6. confidence 归一化 ───────────────────────────────────────────────────────
console.log('\n[6] confidence 归一化');

test('缺失 → 0；字符串数字 → 转成数字；越界 → 夹到边界', () => {
  assert.equal(run(json({ verdict: 'pass', blocking: [], findings: [finding()] })).confidence, 0);
  assert.equal(run(json({ verdict: 'pass', confidence: '0.6', blocking: [], findings: [finding()] })).confidence, 0.6);
  assert.equal(run(json({ verdict: 'pass', confidence: 3, blocking: [], findings: [finding()] })).confidence, 1);
  assert.equal(run(json({ verdict: 'pass', confidence: -2, blocking: [], findings: [finding()] })).confidence, 0);
  assert.equal(run(json({ verdict: 'pass', confidence: '很高', blocking: [], findings: [finding()] })).confidence, 0);
});

// ── 7. assertVerdict 真的在拦 ─────────────────────────────────────────────────
console.log('\n[7] assertVerdict 负例');

test('非法裁决一律抛错（这是最后一道闸）', () => {
  const bad = [
    null,
    {},
    { verdict: 'pass', confidence: 0.5, blocking: ['x'], findings: [] }, // pass + blocking
    { verdict: 'fail', confidence: 0.5, blocking: [], findings: [] }, // 第四态
    { verdict: 'pass', confidence: 2, blocking: [], findings: [] }, // confidence 越界
    { verdict: 'pass', confidence: 0.5, blocking: [{}], findings: [] }, // blocking 不是字符串
    { verdict: 'pass', confidence: 0.5, blocking: [], findings: [{ id: 'C1', claim: 'x', evidence: '', type: 'E9' }] },
    { verdict: 'pass', confidence: 0.5, blocking: [], findings: [{ id: '', claim: 'x', evidence: '', type: 'E0' }] },
  ];
  for (const value of bad) {
    assert.throws(() => assertVerdict(value), /裁决不合规/, `应拒绝：${JSON.stringify(value)}`);
  }
});

test('normalizeVerdict 的产物永远能过 assertVerdict（对 20 组垃圾输入）', () => {
  const garbage = [
    '{"verdict":"pass"}',
    '{"verdict":"PASS","confidence":1}',
    '{"verdict":" rework ","confidence":0.1,"blocking":[1,2,3]}',
    '{"verdict":null}',
    '{"verdict":"escalate","blocking":"不是数组"}',
    '{"verdict":"pass","findings":[null,42,"x"]}',
    '```json\nnot json at all\n```',
    '{}',
    '[]',
    'null',
    '{"verdict":"pass","confidence":null,"blocking":[],"findings":[{"id":"C1","claim":"a","evidence":"e:1","type":"E3"}]}',
    '{"verdict":"pass","confidence":0.5,"blocking":[],"findings":[{"id":"C1","claim":"a","evidence":"e:1","type":"e2"}]}',
    '{"Verdict":"rework","Confidence":0.5,"Blocking":["b"]}',
    '{"verdict":"rework","blocking":[{"id":"B1","what":"w","why":"y","evidence":"z"}]}',
    '{"verdict":"pass","blocking":[],"findings":[]} trailing junk',
    '```json\n{"verdict":"pass","confidence":0.5}\n```\n```json\n{"verdict":"escalate"}\n```',
    '{"verdict":"pass","confidence":0.5,"blocking":[],"findings":[{"id":"C1","claim":"a","evidence":"e:1","type":"E2"}],"extra":"ignored"}',
    '{"verdict":"escalate","blocking":[]}',
    '{"verdict":"rework","confidence":0,"blocking":["x"],"findings":[{"id":"F1","claim":"c","evidence":"","type":"E1"}]}',
    '{"verdict":"pass","confidence":0.5,"blocking":[],"findings":[{"id":"C1","claim":"a","evidence":"e:1","claimed_strength":"E4"}]}',
  ];
  for (const input of garbage) {
    const v = normalizeVerdict(parseVerdict(input));
    assert.ok(VERDICTS.includes(v.verdict), `${input} → 非法 verdict ${v.verdict}`);
    assert.ok(v.confidence >= 0 && v.confidence <= 1, `${input} → confidence 越界`);
    assert.doesNotThrow(() => assertVerdict(v), `${input} 的归一化产物过不了 final gate`);
  }
});

// ── 8. 历史真实裁决（存在才跑）────────────────────────────────────────────────
console.log('\n[8] 历史真实裁决（docs/decisions/*.json）');

const decisionsDir = path.join(REPO_ROOT, 'docs', 'decisions');
if (!existsSync(decisionsDir)) {
  console.log('  skip  docs/decisions 不存在（本用例只在本仓库内有效）');
} else {
  test('116 份历史裁决：JSON 合法的都能被解析，且归一化产物合规', () => {
    const files = readdirSync(decisionsDir).filter((file) => file.endsWith('.json'));
    assert.ok(files.length > 0, 'docs/decisions 里应当有裁决 JSON');
    let parsedCount = 0;
    let brokenCount = 0;
    for (const file of files) {
      const text = readFileSync(path.join(decisionsDir, file), 'utf8');
      let fileVerdict = null;
      try {
        // 历史文件里有带 BOM 的（PowerShell 写出来的），判「JSON 坏没坏」要先剥 BOM
        fileVerdict = JSON.parse(text.replace(/^\uFEFF/, '')).verdict;
      } catch {
        // 真有坏 JSON 时（字符串引号未转义这类）：必须降级 escalate，而不是被当 pass
        brokenCount += 1;
        const degraded = normalizeVerdict(parseVerdict(text));
        assert.equal(degraded.verdict, 'escalate', `坏 JSON ${file} 必须降级 escalate`);
        assert.doesNotThrow(() => assertVerdict(degraded));
        continue;
      }
      if (!VERDICTS.includes(fileVerdict)) continue; // 空 verdict 的历史文件不在断言范围
      const v = run(text);
      assert.ok(VERDICTS.includes(v.verdict), `${file} → 非法 verdict`);
      assert.ok(v.confidence >= 0 && v.confidence <= 1, `${file} → confidence 越界`);
      assert.doesNotThrow(() => assertVerdict(v), `${file} 的归一化产物过不了 final gate`);
      parsedCount += 1;
    }
    console.log(`       解析 ${parsedCount} 份 / 坏 JSON ${brokenCount} 份（都降级 escalate）/ 共 ${files.length} 份`);
    assert.ok(parsedCount > 50, '至少应当解析出 50 份历史裁决');
  });

  test('历史文件里的 rework（blocking 非空）不会漏成 pass', () => {
    const files = readdirSync(decisionsDir).filter((file) => file.endsWith('.json'));
    let reworkSeen = 0;
    for (const file of files) {
      const text = readFileSync(path.join(decisionsDir, file), 'utf8');
      let data;
      try {
        data = JSON.parse(text.replace(/^\uFEFF/, ''));
      } catch {
        continue;
      }
      if (data.verdict !== 'rework') continue;
      const v = run(text);
      assert.notEqual(v.verdict, 'pass', `${file} 历史是 rework，归一化后不许变 pass`);
      reworkSeen += 1;
    }
    console.log(`       核过 ${reworkSeen} 份历史 rework`);
    assert.ok(reworkSeen > 0, 'docs/decisions 里应当有 rework 裁决');
  });
}

// ── 汇总 ──────────────────────────────────────────────────────────────────────
console.log(`\n${'─'.repeat(60)}`);
if (failures.length === 0) {
  console.log(`PASS ${passed} / ${passed}`);
  process.exitCode = 0;
} else {
  console.log(`FAIL ${failures.length} / ${passed + failures.length}`);
  for (const failure of failures) console.log(`  - ${failure.name}: ${failure.message}`);
  process.exitCode = 1;
}
