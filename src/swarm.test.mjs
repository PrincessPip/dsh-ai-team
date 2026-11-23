/**
 * `swarm.js` 的断言机 —— 直接跑：`node src/swarm.test.mjs`
 *
 * 蜂群模式的**唯一可验部分**（其余要靠真会话看模型有没有去调 `ai_team_plan`）：
 *   1. 开关读取：普通布尔、缺省、以及运行时的 `Volatile` 引用三种形状
 *   2. section 正文：开=提示词、关=空串（空 section 由装配器丢弃）
 *   3. 提示词里该有的要素：三个工具名、例外声明通道、关闭方式
 *   4. `Config` 里 `swarmMode` 默认 false **且**声明为 volatile（否则中途切不生效）
 *   5. `apply()` 把 section 挂上去，且 `text()` 每次调用都重读开关（不是 apply 时定死）
 *
 * 退出码：0 = 全绿；1 = 有失败。
 */
import { strict as assert } from 'node:assert';

import { Config, DEFAULTS, apply } from './index.js';
import { SWARM_PROMPT, SWARM_SECTION_NAME, SWARM_SECTION_ORDER, readSwarmMode, swarmSectionText } from './swarm.js';

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

/** 最小可用的 cordis 替身：够 `apply()` 走完装配，不多做。 */
function fakeCtx() {
  /** @type {object[]} */
  const sections = [];
  /** @type {object[]} */
  const registered = [];
  return {
    sections,
    registered,
    tools: {
      register(tool) {
        registered.push(tool);
        return () => {};
      },
    },
    inject(deps, callback) {
      assert.deepEqual(deps, ['systemPrompt'], 'apply 只应额外要求 systemPrompt');
      callback({
        effect(fn) {
          return fn();
        },
        systemPrompt: {
          section(desc) {
            sections.push(desc);
            return () => {};
          },
        },
      });
      return () => {};
    },
  };
}

console.log('\n── readSwarmMode ──────────────────────────────────────────');

test('缺省（undefined / 空对象）算关闭', () => {
  assert.equal(readSwarmMode(undefined), false);
  assert.equal(readSwarmMode(null), false);
  assert.equal(readSwarmMode({}), false);
});

test('普通布尔照原样读', () => {
  assert.equal(readSwarmMode({ swarmMode: true }), true);
  assert.equal(readSwarmMode({ swarmMode: false }), false);
});

test('volatile 引用走 get()（运行时就是这种形状）', () => {
  assert.equal(readSwarmMode({ swarmMode: { get: () => true } }), true);
  assert.equal(readSwarmMode({ swarmMode: { get: () => false } }), false);
});

test('非布尔垃圾值不算开启', () => {
  assert.equal(readSwarmMode({ swarmMode: 'true' }), false);
  assert.equal(readSwarmMode({ swarmMode: 1 }), false);
  assert.equal(readSwarmMode({ swarmMode: { get: () => 'yes' } }), false);
});

console.log('\n── swarmSectionText / SWARM_PROMPT ────────────────────────');

test('关闭时是空串（不是空段落）', () => {
  assert.equal(swarmSectionText(false), '');
  assert.equal(swarmSectionText(undefined), '');
});

test('开启时就是提示词正文', () => {
  assert.equal(swarmSectionText(true), SWARM_PROMPT);
  assert.ok(SWARM_PROMPT.includes('调度员'), '要点明角色变化');
});

test('三个工具名都在（模型要照着调）', () => {
  for (const tool of ['ai_team_plan', 'ai_team_gate', 'ai_team_run']) {
    assert.ok(SWARM_PROMPT.includes(tool), `提示词缺 ${tool}`);
  }
});

test('例外通道写法固定，事后可机检', () => {
  assert.ok(SWARM_PROMPT.includes('[蜂群例外:'), '必须有显式偏离声明');
  assert.ok(SWARM_PROMPT.includes('不写声明就绕过 = 违规'), '必须点明不声明即违规');
});

test('说清了只读不受限与关闭方式', () => {
  assert.ok(SWARM_PROMPT.includes('只读操作'), '只读必须豁免');
  assert.ok(SWARM_PROMPT.includes('蜂群 ON'), '关闭方式要指到输入框开关');
});

test('section 名与顺序是模块内唯一真值', () => {
  assert.equal(SWARM_SECTION_NAME, 'ai-team:swarm-mode');
  assert.ok(SWARM_SECTION_ORDER > 600 && SWARM_SECTION_ORDER < 800, '落在 TEAM_POLICY 与 PTC_ONLY 之间');
});

console.log('\n── Config ────────────────────────────────────────────────');

test('swarmMode 默认 false 且是 volatile 引用', () => {
  assert.equal(DEFAULTS.swarmMode, false);
  const parsed = Config({});
  assert.equal(typeof parsed.swarmMode.get, 'function', 'volatile 字段解析后应有 get()');
  assert.equal(parsed.swarmMode.get(), false);
});

test('swarmMode 显式给值能读回', () => {
  assert.equal(Config({ swarmMode: true }).swarmMode.get(), true);
});

console.log('\n── apply ─────────────────────────────────────────────────');

test('apply 注册 5 个工具 + 1 段蜂群 section', () => {
  const ctx = fakeCtx();
  apply(ctx, {});
  assert.equal(ctx.registered.length, 5, '五个工具都要注册');
  assert.equal(ctx.sections.length, 1, '只注入一段蜂群提示');
  assert.equal(ctx.sections[0].name, SWARM_SECTION_NAME);
  assert.equal(ctx.sections[0].order, SWARM_SECTION_ORDER);
});

test('section.text() 每轮重读开关（中途切立刻反映）', () => {
  const ctx = fakeCtx();
  let current = false;
  const config = { swarmMode: { get: () => current } };
  apply(ctx, config);
  const section = ctx.sections[0];

  assert.equal(section.text(), '', '关着的时候不该有内容');
  current = true;
  assert.equal(section.text(), SWARM_PROMPT, '开了之后下一轮就要有内容');
  current = false;
  assert.equal(section.text(), '', '再关掉要回到空');
});

test('apply 对空 config 也稳（cordis 之外的直接调用）', () => {
  const ctx = fakeCtx();
  apply(ctx, undefined);
  assert.equal(ctx.sections[0].text(), '', '没有 config 时按默认关闭');
});

// ── 汇总 ────────────────────────────────────────────────────────
console.log(`\n${'─'.repeat(60)}`);
if (failures.length === 0) {
  console.log(`PASS ${passed} / ${passed}`);
  process.exitCode = 0;
} else {
  console.log(`FAIL ${failures.length} / ${passed + failures.length}`);
  for (const failure of failures) console.log(`  - ${failure.name}: ${failure.message}`);
  process.exitCode = 1;
}
