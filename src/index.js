/**
 * `dsh-ai-team` 插件入口（SPEC §2）。
 *
 * 一句话定位：DSH 官方 agent-team 解决「谁来干」，本插件解决「**干得对不对**」——
 * 独立验收官、三态裁决、无证据不算通过、DAG 出图与波内并行。
 *
 * 导出契约（照抄 `dsh-gsv-tts` 与 `@deepseek-ai/dsh-experimental-tool-agent-team` 的范式）：
 *   `name` / `inject` / `Config` / `apply`
 *
 * `inject` 只有 `tools`：五个工具都只往工具注册表里挂东西，
 * 会话工作目录通过 `ctx.get('workingDirectory')` **按需取**（取不到才退回进程 cwd，
 * 见 `lib/paths.js` 的 `cwdOf`），所以不需要把 `workingDirectory` 写进 inject 里、
 * 也不会因为某个 profile 没装它而整套插件加载失败。
 *
 * @module
 */
import Schema from '@deepseek-ai/schemastery';
import { SWARM_SECTION_NAME, SWARM_SECTION_ORDER, readSwarmMode, swarmSectionText } from './swarm.js';
import { createGateTool } from './tools/gate.js';
import { createInitTool } from './tools/init.js';
import { createPlanTool } from './tools/plan.js';
import { createReviewTool } from './tools/review.js';
import { createRunTool } from './tools/run.js';

/** 插件名（cordis 用它对账 patch 里的 `name`）。 */
export const name = 'ai-team';

/** MVP 只依赖工具注册表；别的服务一律 `ctx.get()` 按需取。 */
export const inject = ['tools'];

/**
 * 配置默认值（**唯一真值**）：Schema 默认值从这里取，`apply` 里也用它补齐，
 * 这样插件在 cordis 之外被直接引用（测试、脚本）时行为一致。
 */
export const DEFAULTS = Object.freeze({
  backend: 'codex',
  model: '',
  codexPath: 'codex',
  // 默认 workspace-write：`-s read-only` 在 Windows 上会禁掉一切命令（连读文件都被 block），
  // 验收官等于什么都看不到（`engine/runner.py:1436-1440` 有工程自己的实测记录）。
  // 所以「只读」不靠沙箱硬隔离，靠证据包 + 事后快照核 → review 里如实标 enforcement=snapshot_only。
  codexSandbox: 'workspace-write',
  openaiBaseUrl: 'https://api.openai.com/v1',
  openaiApiKeyEnv: 'OPENAI_API_KEY',
  timeoutMs: 3600000,
  engineDir: '',
  pythonPath: 'python',
  nodePath: 'node',
  outDirName: '.ai-team',
  swarmMode: false,
});

/**
 * 配置契约（SPEC §4）。
 *
 * 安全红线：`openaiApiKeyEnv` 存的是**环境变量名**，不是 key —— 本插件任何地方都不接受 key 字面量。
 */
export const Config = Schema.object({
  backend: Schema.union([Schema.const('codex'), Schema.const('openai')])
    .default(DEFAULTS.backend)
    .description('验收官后端'),
  model: Schema.string().default(DEFAULTS.model).description('默认模型，留空用后端默认'),
  codexPath: Schema.string().default(DEFAULTS.codexPath).description('codex CLI 可执行文件路径'),
  codexSandbox: Schema.union([
    Schema.const('read-only'),
    Schema.const('workspace-write'),
    Schema.const('danger-full-access'),
  ])
    .default(DEFAULTS.codexSandbox)
    .description(
      'codex 沙箱档位（backend=codex 时生效）。默认 workspace-write —— Windows 上 read-only 会禁掉一切命令，' +
        '只读由证据包 + 事后快照核承担，不是硬隔离',
    ),
  openaiBaseUrl: Schema.string()
    .default(DEFAULTS.openaiBaseUrl)
    .description('OpenAI 兼容 API 基址'),
  openaiApiKeyEnv: Schema.string()
    .default(DEFAULTS.openaiApiKeyEnv)
    .description('存放 API Key 的环境变量名（不要把 key 写进配置）'),
  timeoutMs: Schema.number().default(DEFAULTS.timeoutMs).description('单次验收超时'),
  engineDir: Schema.string().default(DEFAULTS.engineDir).description('引擎目录，留空用包内 engine/'),
  pythonPath: Schema.string().default(DEFAULTS.pythonPath).description('Python 可执行文件'),
  nodePath: Schema.string().default(DEFAULTS.nodePath).description('Node 可执行文件'),
  outDirName: Schema.string().default(DEFAULTS.outDirName).description('运行产物目录名'),
  // volatile：开关必须能在**会话进行中**热改。loader 对 volatile-only 变更不重挂插件，
  // 只把新值写进运行中的引用（`_commitVolatile`），所以任何一处读到 `config.swarmMode`
  // 都拿到当前值；客户端（`src/client.js`）就是通过 configForms 写这个字段的。
  swarmMode: Schema.boolean()
    .default(DEFAULTS.swarmMode)
    .volatile()
    .description('蜂群模式：开启后把 Lead 降为调度员，实现类任务必须走 ai_team_plan → gate → run'),
});

/** 五个工具工厂，顺序 = 注册顺序（也是推荐使用顺序：init → review / plan → gate → run）。 */
const TOOL_FACTORIES = Object.freeze([createInitTool, createReviewTool, createPlanTool, createGateTool, createRunTool]);

/**
 * 挂载插件：把五个工具注册进 `ctx.tools`。
 *
 * @param {object} ctx cordis 上下文（需要 `tools` 服务）。
 * @param {object} [config] 插件配置（缺项用 {@link DEFAULTS} 补齐）。
 */
export function apply(ctx, config) {
  const resolved = { ...DEFAULTS, ...(config ?? {}) };
  for (const createTool of TOOL_FACTORIES) {
    ctx.tools.register(createTool(ctx, resolved));
  }

  // 蜂群模式：一段**每轮重新调用**的 system prompt section。
  // 开关值来自原始 `config`（volatile 引用），不能用 `resolved` —— 展开会把引用固定成
  // apply 那一刻的布尔值，之后热改就看不见了。
  // `systemPrompt` 按需注入：profile 没装它时，五个工具照常可用。
  ctx.inject(['systemPrompt'], (spCtx) => {
    spCtx.effect(
      () =>
        spCtx.systemPrompt.section({
          name: SWARM_SECTION_NAME,
          order: SWARM_SECTION_ORDER,
          text: () => swarmSectionText(readSwarmMode(config)),
        }),
      `${name}: swarm-mode prompt section`,
    );
  });
}
