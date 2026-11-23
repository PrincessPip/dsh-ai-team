// dag-gate.mjs —— 把一张 GRAPH.json 丢进 `scripts/dag.workflow.mjs` 的 Gate 里过一遍
//
// 为什么单独存在（DESIGN §8 Step 1）：
//   经理层产出的是**纯数据**（GRAPH.json），它合不合法不能由经理自己说了算，
//   也不能由调用它的脚本另写一套校验 —— 那会立刻出现「两份 Gate」，一改就漂。
//   所以这里**直接读 `dag.workflow.mjs` 的脚本体**，用与引擎同形的包法跑它的 dry-run 分支。
//
// 它复用的是什么：`args.dryRun=true` + `args.graphJson` 这条既有入参路（dag.workflow.mjs:545 / :553 / :577）。
//   也就是说：Gate 是通过的哪条代码，这里就是哪条代码 —— 不复制判据，只复制入口。
//
// 用法：
//   node scripts/dag-gate.mjs <GRAPH.json>           人读摘要 + 退出码
//   node scripts/dag-gate.mjs <GRAPH.json> --json    只出机器可读 JSON（给 manager.py 用）
// 退出码：0 = 通过（可能有 warn）/ 1 = 被 Gate 拒收 / 2 = 读不到或不是合法 JSON
//
// 反证内建：假 `agent()` 一被调用就抛错 —— 只要这个脚本成功返回，就证明「Gate 阶段零 agent」不是自称。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.join(here, 'dag.workflow.mjs'), 'utf8');

const argv = process.argv.slice(2);
const jsonOnly = argv.indexOf('--json') >= 0;
const file = argv.find((a) => a !== '--json');

const die = (code, msg) => {
  if (jsonOnly) console.log(JSON.stringify({ mode: 'gate', ok: false, fatal: msg, errs: [], warns: [] }, null, 2));
  else console.error('[gate] ' + msg);
  process.exit(code);
};

if (!file) die(2, '用法：node scripts/dag-gate.mjs <GRAPH.json> [--json]');
if (!fs.existsSync(file)) die(2, '文件不存在：' + file);

let raw = fs.readFileSync(file, 'utf8');
// 去掉 BOM：Windows 编辑器另存为 UTF-8 带签名时，JSON.parse 会直接失败
if (raw.charCodeAt(0) === 0xfeff) raw = raw.slice(1);
if (!raw.trim()) die(2, '文件是空的：' + file);

// 先自己 parse 一次：这样「不是合法 JSON」和「图不合法」是两个不同的退出码，不会混成一条
let graph;
try {
  graph = JSON.parse(raw);
} catch (e) {
  die(2, '不是合法 JSON：' + (e && e.message));
}

// 与引擎同形的包法（见 tasks/DAG-2026-11-06/harness.mjs:64-66）
const wrap = (body) =>
  new Function('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'return (async () => {\n' + body + '\n})()');

const fn = wrap(SRC);
const mustNotCallAgent = () => {
  throw new Error('Gate 阶段竟然调了 agent —— 这是 dry-run 分支被改坏了的证据');
};

let out;
try {
  out = await fn({ dryRun: true, graphJson: raw }, mustNotCallAgent, undefined, undefined, () => {}, () => {});
} catch (e) {
  die(2, '脚本体抛错：' + (e && e.message));
}

const errs = Array.isArray(out && out.errs) ? out.errs : [];
const warns = Array.isArray(out && out.warns) ? out.warns : [];
const ok = Boolean(out && out.ok);

const result = {
  mode: 'gate',
  file: path.resolve(file),
  ok: ok,
  plan_id: (out && out.plan_id) || null,
  agentCalls: 'never (fake agent throws)', // 上面 mustNotCallAgent 存在即证明
  errCount: errs.length,
  warnCount: warns.length,
  errs: errs,
  warns: warns,
  // dry-run 命中时的调度计划 —— 人点头要对着它核（DESIGN §3 ③）
  waves: (out && out.waves) || null,
  waveCount: (out && out.waveCount) === undefined ? null : out.waveCount,
  criticalPath: (out && out.criticalPath) || null,
  criticalLen: (out && out.criticalLen) === undefined ? null : out.criticalLen,
  nodeCount: (out && out.nodeCount) === undefined ? null : out.nodeCount,
  schedule: (out && out.schedule) || null,
};

if (jsonOnly) {
  console.log(JSON.stringify(result, null, 2));
} else {
  const line = (s) => console.log(s);
  line('[gate] file       ' + result.file);
  line('[gate] plan_id    ' + (result.plan_id || '(缺)'));
  line('[gate] agents     ' + result.agentCalls);
  if (!ok) {
    line('[gate] result     REJECTED  (' + errs.length + ' error / ' + warns.length + ' warn)');
    for (const e of errs) line('  [err ] ' + e.code + '  ' + e.msg);
  } else {
    line('[gate] result     OK  (' + errs.length + ' error / ' + warns.length + ' warn)');
    line('[gate] nodes      ' + result.nodeCount + '  waves ' + result.waveCount);
    line('[gate] critical   ' + (result.criticalPath || []).join(' → ') + '  (' + result.criticalLen + ' 节点)');
    for (let i = 0; i < (result.waves || []).length; i++) {
      line('  [wave' + i + '] ' + result.waves[i].join(' · '));
    }
  }
  for (const w of warns) line('  [warn] ' + w.code + '  ' + w.msg);
}

process.exit(ok ? 0 : 1);
