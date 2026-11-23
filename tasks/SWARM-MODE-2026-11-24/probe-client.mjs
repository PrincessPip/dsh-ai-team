/**
 * 蜂群模式 · 浏览器半边探针（零浏览器、零服务器）。
 *
 * 目的：让「刷新页面后开关能不能挂上」这件事在**我这边**先过一遍，
 * 而不是让 UI 失败变成一次白跑的人工验收。做法是伪造最小 `window.__ModuleLoader__` /
 * `document` / `react` / cordis 客户端 ctx，加载真实的 `src/client.js`，
 * 然后断言它注册的槽位描述与官方 `conversation.input.permission` 契约一致。
 *
 * 它验什么：模块能否加载、`apply` 能否跑完、槽位名/order/inject 形状、locale 与样式是否注册、
 * `configForms.get` 的 entryId 是否与插件行 id 一致。
 * 它不验什么：真实渲染（hooks 不能在裸函数里跑）、真实写回（要 Host 的 settings 文档）。
 *
 * 跑法：`node tasks/SWARM-MODE-2026-11-24/probe-client.mjs`
 * 退出码：0 = 全绿；1 = 有失败。
 */
import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLIENT = path.resolve(HERE, '..', '..', 'src', 'client.js');
const MANIFEST = path.resolve(HERE, '..', '..', 'package.json');

let passed = 0;
/** @type {{ name: string, message: string }[]} */
const failures = [];
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

/** 最小 React：本探针不渲染，只要 createElement 与两个 hook 的名字在。 */
const React = {
  createElement: () => null,
  useState: (initial) => [initial, () => {}],
  useEffect: () => {},
};

/** 收集来的槽位注册描述。 */
const registrations = [];
const localeDicts = [];
const injectedStyles = [];

const fakeDocument = {
  getElementById: () => null,
  head: {
    appendChild(node) {
      injectedStyles.push(node);
      return node;
    },
  },
  createElement(tag) {
    return { tag, id: '', textContent: '', parentNode: null };
  },
};

/** 假 form：只记录 entryId，验证客户端拿的是插件行 id。 */
const formRequests = [];
const fakeForm = {
  getSnapshot: () => ({ status: 'ready', value: { swarmMode: false }, writable: true, revision: 1 }),
  subscribe: () => () => {},
  set: async () => true,
  unset: async () => true,
  mutate: async () => true,
};

const fakeCtx = {
  effect: (fn) => fn(),
  get: () => undefined,
  slots: {
    inject(name, callback) {
      registeredSlotNames.push(name);
      return callback();
    },
    register(descriptor) {
      registrations.push(descriptor);
      return () => {};
    },
  },
  locale: {
    register(ns, dict) {
      localeDicts.push({ ns, dict });
      return () => {};
    },
    bind: (ns) => (key) => `${ns}:${key}`,
  },
  configForms: {
    get(entryId) {
      formRequests.push(entryId);
      return fakeForm;
    },
  },
};
const registeredSlotNames = [];

/** 加载真实 client.js：伪造 ModuleLoader 与浏览器全局。 */
const source = readFileSync(CLIENT, 'utf8');
let loaded;
const sandbox = {
  window: {
    __ModuleLoader__: {
      load(definition) {
        loaded = definition;
      },
    },
  },
  document: fakeDocument,
  console,
  require: (name) => {
    if (name === 'react') return React;
    throw new Error(`未预期的 require: ${name}`);
  },
};
vm.createContext(sandbox);
new vm.Script(source, { filename: CLIENT }).runInContext(sandbox);

console.log('\n── 蜂群模式 · 浏览器半边探针 ──────────────────────');

test('模块被 __ModuleLoader__ 以 id=dsh-ai-team 加载', () => {
  assert.ok(loaded, 'client.js 没有调用 window.__ModuleLoader__.load');
  assert.equal(loaded.id, 'dsh-ai-team');
  assert.equal(typeof loaded.factory, 'function');
});

const exported = loaded.factory(sandbox.require);

test('导出形状：name / inject / apply', () => {
  assert.equal(exported.name, 'dsh-ai-team/client');
  assert.ok(Array.isArray(exported.inject), 'inject 要是数组');
  assert.equal(typeof exported.apply, 'function');
});

test('声明的服务齐全：slots / locale / configForms', () => {
  for (const service of ['slots', 'locale', 'configForms']) {
    assert.ok(exported.inject.includes(service), `缺 inject: ${service}`);
  }
});

test('apply 能跑完（不抛错）', () => {
  assert.doesNotThrow(() => exported.apply(fakeCtx));
});

test('注册到 list 型槽 conversation.input.left（不是 single 型权限槽）', () => {
  assert.deepEqual(registeredSlotNames, ['conversation.input.left']);
  assert.equal(registrations.length, 1, '应当只注册一个 occupant');
  assert.equal(registrations[0].name, 'conversation.input.left');
});

test('绝不注册到 kind=single 的槽（会顶掉官方控件）', () => {
  // 契约出处：dsh-client-ui-conversation/lib/types/client/contract/slots.d.ts
  // conversation.input.permission / .plan / .model / .attachments 都是 kind: 'single'，
  // 单占位只渲染一个 occupant —— 注册进去会让官方控件整颗消失（2026-11-24 实测踩过）。
  const singleSlots = [
    'conversation.input.permission',
    'conversation.input.plan',
    'conversation.input.model',
    'conversation.input.attachments',
    'conversation.input.activity',
    'conversation.composer.bar',
    'conversation.session',
    'conversation.header',
  ];
  for (const name of registeredSlotNames) {
    assert.ok(!singleSlots.includes(name), `不得占用单占位槽 ${name}`);
  }
});

test('order 为负（list 槽的第一位才紧贴权限控件）', () => {
  const order = registrations[0].order;
  assert.equal(typeof order, 'number', 'order 必须是数字');
  // dsh-voice-local 的麦克风按钮是 order: -100；本开关要比它更靠前，才落在权限与语音之间。
  assert.ok(order < -100, `order 应 < -100（语音插件是 -100），实际 ${order}`);
});

test('inject 提供了 form，且给的是插件行 id', () => {
  const face = registrations[0].inject();
  assert.ok(face && face.form, 'inject 应返回 { form }');
  assert.deepEqual(formRequests, ['dsh-ai-team'], `configForms.get 应拿 'dsh-ai-team'，实际 ${JSON.stringify(formRequests)}`);
});

test('locale 字典带了 on / off 两态文案', () => {
  assert.equal(localeDicts.length, 1, '应注册一份字典');
  const { zh, en } = localeDicts[0].dict;
  assert.equal(zh.on, '蜂群 ON');
  assert.equal(zh.off, '蜂群 OFF');
  assert.ok(en.on && en.off, '英文两态也要有');
});

test('样式注入一次，且用的是语义/静态色变量', () => {
  assert.equal(injectedStyles.length, 1, '应注入一份 <style>');
  const css = injectedStyles[0].textContent;
  assert.ok(css.includes('--dsw-alias-border-l4'), 'OFF 态描边要用语义变量');
  assert.ok(css.includes('--dsw-static-deepseek-450'), 'ON 态底色要用品牌静态蓝');
  assert.ok(!/background:\s*#fff\b/i.test(css), '不得硬编码白底（暗色主题会变白斑）');
});

test('package.json 的 client 入口与插件行 id 对得上', () => {
  const manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'));
  assert.equal(manifest.exports['./client'], './src/client.js', 'exports 要指向 src/client.js');
  assert.equal(manifest.dsh.client.platform, 'web');
  for (const pkg of ['@deepseek-ai/dsh-client-locale', '@deepseek-ai/dsh-client-ui-slots', '@deepseek-ai/dsh-client-ui-settings']) {
    assert.ok(manifest.dsh.client.inject.includes(pkg), `dsh.client.inject 缺 ${pkg}`);
  }
  const patch = readFileSync(path.resolve(HERE, '..', '..', 'cordis.patch.yml'), 'utf8');
  assert.ok(/^\s+- id: dsh-ai-team$/m.test(patch), 'cordis.patch.yml 的插件行 id 必须是 dsh-ai-team');
});

console.log(`\n${'─'.repeat(60)}`);
if (failures.length === 0) {
  console.log(`PASS ${passed} / ${passed}`);
  process.exitCode = 0;
} else {
  console.log(`FAIL ${failures.length} / ${passed + failures.length}`);
  for (const failure of failures) console.log(`  - ${failure.name}: ${failure.message}`);
  process.exitCode = 1;
}
