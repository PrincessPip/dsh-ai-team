// selfcheck.workflow.mjs - body for the DSH `workflow` tool (self-check pipeline)
//
// WHY: `decisions/LESSONS.md` lists five error classes and seven checks that keep
//      costing a review round. This pipeline runs those checks in PARALLEL
//      before a delivery goes to the reviewer, so the obvious problems are gone
//      before they cost a rework round.
//
// WHAT IT IS NOT: it is not the reviewer. It is the same model family checking
//      its own work -- "all agents pretend they executed" is exactly the failure
//      mode this cannot fix. Its output is PRE-processing, never a pass.
//
// USAGE (from the chat, not by hand): pass this file's body as the `script`
//      argument of the `workflow` tool, with
//      args = { unit, taskPath, deliveryPath, files: [...], checks: [...] }
//      `checks` picks a subset by id; omit it to run all seven.
//
// COST: every check is one agent call on the inherited route (deepseek), i.e.
//       cheap. Run 2-3 checks on small work, all seven before a real submit.

const CHECKS = {
  numbers: {
    id: 'numbers',
    title: '数字自洽',
    how: '把交付说明里出现的每一个计数（几个、N 组、共 M 条）与它后面实际枚举出来的条目逐一点数核对；分项之和与总数也要相等。任何「N 个」的标题都必须能数出 N 条。',
  },
  claims: {
    id: 'claims',
    title: '声明 vs 证据',
    how: '找出所有「已修复 / 已验证 / 全部通过 / 0 命中 / 无问题」这类断言，逐条检查有没有对应的命令输出或 file:line 支撑。没有输出支撑的断言算问题；同一份文件别处若出现了与该断言矛盾的事实，另记一条。',
  },
  crossfile: {
    id: 'crossfile',
    title: '跨文件同步',
    how: '同一事实是否出现在多个文件里且表述不一致。用 Grep 搜旧措辞（旧数字、旧版本号、旧结论），任何命中都要报。',
  },
  strength: {
    id: 'strength',
    title: '类型与强度',
    how: '按 REVIEW-CONTRACT.md §二·五 核对：可执行类必须 E3（≥2 个不同 D 类别 + ≥1 条反证）；判断类上限 E1 且必须标 [判断类]；事实一致类 E2 起。声称 E3 但只有一个观察维度、或没有反证的，直接报。',
  },
  verifiable: {
    id: 'verifiable',
    title: '验收官能否自己核',
    how: '验收官只有 Read/Grep/Glob，没有 shell。逐条看交付说明里的**证据写法**怎么写的，判据是这一条：结论只靠「我跑过了 / 实测没问题 / 我看过了 / 验证过 / 放心」这类自述支撑，既没有 file:line，也没有可 Read 的产物路径 + 字段名，也没有命令输出原文的，一律报。哪怕结论事实上是对的 —— 你核不了就是你核不了，这条检查的就是「你能不能核」。依赖「跑脚本才知道结果」的验收标准，必须至少给出源码控制流（file:line）或落盘产物（可 Read 的路径 + 具体字段名）之一。反过来：只要有一处硬依据，就不要报。',
  },
  honesty: {
    id: 'honesty',
    title: '弱点是否如实',
    how: '「弱在哪 / 明确没做」这类段落是否空着，或写「无」。同时检查有没有把已知的洞、未测的情形、环境限制写出来。写「无」但存在明显未测项 = 问题。',
  },
  ragged: {
    id: 'ragged',
    title: '内部自相矛盾',
    how: '交付说明自身前后是否打架：摘要里的数字与正文不一致、表格里的结论与下面的代码块输出不一致、「说了会做 A」但后文没有 A。只报能指出两处原文的硬冲突。',
  },
};

const picked = (args && args.checks && args.checks.length) ? args.checks : Object.keys(CHECKS);
const unknown = picked.filter((k) => !CHECKS[k]);
if (unknown.length) {
  return { error: 'unknown check id(s): ' + unknown.join(',') + '; valid: ' + Object.keys(CHECKS).join(',') };
}

const schema = {
  type: 'object',
  additionalProperties: false,
  required: ['check_id', 'ok', 'issues'],
  properties: {
    check_id: { type: 'string' },
    ok: { type: 'boolean' },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['severity', 'what', 'evidence'],
        properties: {
          severity: { type: 'string', enum: ['blocking', 'nonblocking'] },
          what: { type: 'string' },
          evidence: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
  },
};

phase('selfcheck');
log('running ' + picked.length + ' check(s) on ' + args.unit);

const results = await parallel(picked.map((key) => () => {
  const c = CHECKS[key];
  const lines = [
    '你是只读的自查员，不是验收官，不下 verdict。工作目录就是本工程根目录。',
    '本轮交付单元：' + args.unit,
    '',
    '只看这一条检查项：[' + c.id + '] ' + c.title + ' —— ' + c.how,
    '',
    '材料（自己 Read，不要相信任何摘要）：',
    '- 任务书：' + args.taskPath,
    '- 交付说明：' + args.deliveryPath,
  ];
  if (args.files && args.files.length) lines.push('- 相关文件：' + args.files.join('、'));
  lines.push(
    '',
    '规则：',
    '1. 只读。不许写文件、不许改文件、不许跑任何会改变状态的命令。',
    '2. 只判这一项；看到别类问题不要在这里报，别的检查员会管。',
    '3. 每条问题必须带证据：file:line 或原文片段。引用前自己打开看过。',
    '4. 没问题就 ok=true、issues=[]。不要为了显得有用而编问题。',
    '5. 判不准的宁可不报，也不许猜。',
    '6. 输出的 check_id 字段必须**原样**填 "' + c.id + '"（不要填交付单元名、不要改写）。',
    '',
    '输出严格 JSON，字段见 schema。'
  );
  return agent(lines.join('\n'), { schema, label: c.id, phase: 'selfcheck' });
}));

// Do not trust the agent to echo check_id back: one run returned the unit name.
// Re-stamp it positionally (parallel() preserves input order).
const flat = results
  .map((r, i) => (r ? Object.assign({}, r, { check_id: picked[i] }) : null))
  .filter(Boolean);
return {
  unit: args.unit,
  checks_run: flat.map((r) => r.check_id),
  checks_missing: picked.filter((k) => !flat.some((r) => r.check_id === k)),
  failed_checks: flat.filter((r) => !r.ok).length,
  issues: flat.flatMap((r) => (r.issues || []).map((i) => ({ check: r.check_id, severity: i.severity, what: i.what, evidence: i.evidence, fix: i.fix }))),
};
