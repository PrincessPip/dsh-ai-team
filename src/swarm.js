/**
 * 蜂群模式（Swarm Mode）—— 提示词段的**唯一真值**与开关读取。
 *
 * 为什么是提示词而不是收工具：工具表在**会话启动时**装配，中途切换不生效；
 * system prompt 的 section 每轮组装都会重新调用 `text()`，所以「某一轮中间打开」
 * 下一轮就生效（`swarmMode` 是 Schemastery 的 `volatile` 字段，热改直接写进运行中的引用）。
 *
 * 设计枢纽是第 4 条：把「违反」变成「必须显式声明的偏离」——
 * 事后核对不用猜，看回复里有没有 `[蜂群例外: …]` 再对实际干了什么。
 *
 * @module
 */

/** section 名：注册与排重都用它，别在两处写字符串。 */
export const SWARM_SECTION_NAME = 'ai-team:swarm-mode';

/**
 * section 顺序。
 *
 * 官方 `SECTION_ORDERS` 的相邻值是 `TEAM_POLICY: 600` 与 `PTC_ONLY: 800`；
 * 取 700 落在「团队/计划政策」之后、「工具指导」之前，不占用官方已分配的位。
 */
export const SWARM_SECTION_ORDER = 700;

/**
 * 开启时注入的提示词正文。
 *
 * 只约束**实现类**任务，只读问答不受限；例外必须显式声明，不写声明就绕过 = 违规。
 */
export const SWARM_PROMPT = [
  '## 蜂群模式 · 已开启',
  '',
  '你现在是**调度员**，不是工人。',
  '',
  '1. 实现类任务（写/改文件、建文档、跑会改变状态的命令）**必须先 `ai_team_plan` 出图**',
  '   → 图过 `ai_team_gate` 判据 → 由 `ai_team_run` 执行。你不亲自实现。',
  '2. 只读操作（读文件、搜索、只读命令、回答问题）不受此限。',
  '3. 需要拍板的边界，用 `ai_team_plan` 的节点表达，**不要自己决定再告诉我**。',
  '4. **例外通道**：如果你判断本规则不适用（例如只是问答、或规模小到出图反而更慢），',
  '   在回复开头写 `[蜂群例外: 理由]` 然后继续。**不写声明就绕过 = 违规。**',
  '5. 关闭方式：权限控件右边的「蜂群 ON」点一下切成 OFF。',
].join('\n');

/**
 * 读当前开关值。
 *
 * `volatile` 字段在运行中的 config 里是 `Volatile<T>` 引用（只有 `get()`，**没有** valueOf），
 * 所以不能直接当布尔用；插件在 cordis 之外被直接调用（测试、脚本）时则是普通布尔。
 *
 * @param {unknown} config 插件 config（可能是 `{ swarmMode: Volatile<boolean> }` 或普通对象）。
 * @returns {boolean} 是否开启。
 */
export function readSwarmMode(config) {
  const raw = config && typeof config === 'object' ? config.swarmMode : undefined;
  if (raw && typeof raw === 'object' && typeof raw.get === 'function') return raw.get() === true;
  return raw === true;
}

/**
 * 组装 section 正文：开启时返回提示词，关闭时返回空串（空 section 由装配器丢弃）。
 *
 * @param {boolean} enabled 开关值。
 * @returns {string} 该轮的 section 文本。
 */
export function swarmSectionText(enabled) {
  return enabled === true ? SWARM_PROMPT : '';
}
