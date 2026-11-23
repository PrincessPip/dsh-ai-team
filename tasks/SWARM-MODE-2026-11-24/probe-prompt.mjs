/**
 * 蜂群模式 · 提示词装配探针（零模型调用、不碰生产 3080）。
 *
 * 目的：回答「`swarmMode` 打开后，那段提示词**真的**进了 system prompt 吗」——
 * 不是「代码写了所以应该生效」。做法是把官方 `dsh-system-prompt` 服务与
 * dsh-ai-team 插件挂进一个最小 cordis 环境，直接调 `assemble()` 看渲染结果。
 *
 * 跑法：`node tasks/SWARM-MODE-2026-11-24/probe-prompt.mjs`
 * 退出码：0 = 开/关两态都符合预期；1 = 不符合（会把两态渲染一起打出来）。
 */
import { Context } from '@deepseek-ai/cordis';
import SystemPrompt, { renderPrompt } from '@deepseek-ai/dsh-system-prompt';

import * as aiTeam from '../../src/index.js';
import { SWARM_PROMPT } from '../../src/swarm.js';

/** 假工具表：本探针只关心 prompt，不关心工具能不能跑。 */
function fakeTools() {
  return { register: () => () => {} };
}

/**
 * @param {boolean} swarmMode 开关值。
 * @returns {Promise<{ text: string, sections: { name: string, order: number }[] }>}
 */
async function assembleWith(swarmMode) {
  const ctx = new Context();
  ctx.provide('tools', fakeTools());
  await ctx.plugin(SystemPrompt);
  await ctx.plugin(aiTeam, { swarmMode });

  const service = ctx.get('systemPrompt');
  if (!service) throw new Error('systemPrompt 服务没挂上');
  const assembly = await service.assemble();
  const text = renderPrompt(assembly);
  const sections = (assembly.sections ?? []).map((section) => ({ name: section.name, order: section.order }));
  await ctx.fiber?.dispose?.();
  return { text, sections };
}

const MARK = '蜂群模式 · 已开启';

const on = await assembleWith(true);
const off = await assembleWith(false);
/** renderPrompt 用空行连接各段，所以「开-关」的差 = 正文 + 2 个换行。 */
const JOIN = '\n\n';

const results = [];
results.push(['开启：prompt 里有蜂群段', on.text.includes(MARK)]);
results.push(['开启：三个工具名都在', ['ai_team_plan', 'ai_team_gate', 'ai_team_run'].every((tool) => on.text.includes(tool))]);
results.push(['开启：section 已注册', on.sections.some((s) => s.name === 'ai-team:swarm-mode')]);
results.push(['关闭：prompt 里没有蜂群段（空文本被装配器丢弃）', !off.text.includes(MARK)]);
results.push(['关闭：prompt 就是原生的身份段', off.text.trim() === 'You are an AI agent powered by DeepSeek Harness.']);

console.log('\n── 蜂群模式装配探针 ──────────────────────────────');
console.log(`开：prompt ${on.text.length} 字符 / ${on.sections.length} 段`);
console.log(`关：prompt ${off.text.length} 字符 / ${off.sections.length} 段`);
console.log(`开：section 名 = ${on.sections.map((s) => s.name).join(', ') || '(空)'}`);
console.log('');
let failed = 0;
for (const [name, ok] of results) {
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name}`);
  if (!ok) failed += 1;
}

// 开关两态提示词差值必须正好等于「那段正文 + 空行连接符」——证明进出的就是同一段，不是别的东西在变
const delta = on.text.length - off.text.length;
const expect = SWARM_PROMPT.length + JOIN.length;
const deltaOk = delta === expect;
console.log(`  ${deltaOk ? 'ok  ' : 'FAIL'} 两态差值 = 段落长度 + 连接符（${delta} vs ${expect}）`);
if (!deltaOk) failed += 1;

if (failed > 0) {
  console.log('\n=== 开启态 prompt（前 2500 字符）===');
  console.log(on.text.slice(0, 2500));
  console.log('\n=== 关闭态 prompt（前 1200 字符）===');
  console.log(off.text.slice(0, 1200));
  process.exitCode = 1;
} else {
  console.log('\nPASS 全部符合预期');
  process.exitCode = 0;
}
