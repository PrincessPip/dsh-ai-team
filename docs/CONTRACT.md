# 接口契约（CONTRACT）

> **本文是接口层的唯一事实来源。** 实现与本文冲突时以本文为准；要改本文请先开 issue 说明理由。
>
> 它规定的是**对外可观察的接口**：五个工具的参数与返回、Config、后端 adapter、裁决解析规则、
> 自检清单与诚实边界。**不讲架构取舍**（那是 [`ARCHITECTURE.md`](ARCHITECTURE.md)），
> **不讲环境依赖**（那是 [`PORTING.md`](PORTING.md)）。
>
> 目标：把「三权分立 + 三态裁决 + DAG 编排 + 闸门链」做成一个**任何 DSH 用户装得上、跑得起来**的插件。

---

## 0. 定位（一句话）

DSH 官方 agent-team 解决「谁来干」，本插件解决「**干得对不对**」——
独立验收官、三态裁决、无证据不算通过、DAG 出图与波内并行。

**不做的事**（避免与官方撞车）：

- 不重造 teammate / 任务板 / 直投消息（官方 `dsh-experimental-agent-team` 已有）
- 不绑定任何特定模型厂商（后端可插拔）
- 不内置任何私有环境（代理、VPS、订阅）

---

## 1. 仓库结构

```text
dsh-ai-team/
├── package.json              # 包元信息；dsh.bundle.patch 指向 cordis.patch.yml
├── cordis.patch.yml          # profile 挂载（insert 一条插件条目 + Config 默认值）
├── LICENSE                   # MIT
├── .gitignore / .github/     # CI
├── README.md                 # 对外第一印象
├── src/                      # 插件实现
│   ├── index.js              # 插件入口：name / inject / Config / apply
│   ├── tools/
│   │   ├── init.js
│   │   ├── review.js
│   │   ├── plan.js
│   │   ├── gate.js
│   │   └── run.js
│   ├── adapters/
│   │   ├── index.js          # 后端选择
│   │   ├── codex-cli.js      # codex exec 后端
│   │   └── openai-compat.js  # 任意 OpenAI 兼容 API
│   └── lib/
│       ├── verdict.js        # 三态裁决解析 + 降级
│       ├── verdict.test.mjs  # 它的断言机
│       ├── proc.js           # 子进程封装（超时 / kill tree / 编码）
│       ├── paths.js          # 引擎路径解析与自检
│       └── output.js         # jsonOutput 辅助
├── templates/                # 团队模板（`ai_team_init` 的数据源）
│   ├── AGENTS.snippet.md     # → <target>/AI-TEAM.md
│   ├── REVIEW-BRIEF.md       # → <target>/REVIEW-BRIEF.md
│   ├── INC-TEMPLATE.md       # → <target>/INC-TEMPLATE.md
│   └── roles/{worker,reviewer,fire-chief}.md   # → <target>/.ai-team/roles/
├── docs/                     # 架构、移植、契约，以及迁移过来的规则原文与裁决记录
│   ├── CONTRACT.md           # 本文
│   ├── ARCHITECTURE.md
│   ├── PORTING.md
│   ├── REVIEW-CONTRACT.md    # 验收官作业指导书（脱敏版原文）
│   ├── TRIGGER-POLICY.md     # 送审触发判据（脱敏版原文）
│   ├── FIRE-CHIEF.md         # 救火机制
│   ├── INC-TEMPLATE.md       # 事故单模板（原文）
│   ├── PLAN.md               # 方案演进
│   └── decisions/            # 116 份真实裁决 JSON（脱敏）
├── engine/                   # 实战引擎原样（Python / Node）：经理层、Gate、执行器、闸门链
├── tasks/DAG-2026-11-06/fixtures/   # 回归夹具（90 文件 / 约 240 KB）
│                             #   40 个场景（scenarios/*/spec.json）+ 35 张示例图（graphs/*.json）
│                             #   + 假 worker / 假 codex / 沙箱基线
│                             #   ⚠️ **必须保持原相对路径**：引擎里
│                             #   `FAKE_WORKER = ROOT/"tasks"/"DAG-2026-11-06"/"fixtures"/...`，
│                             #   `ROOT = HERE.parent`。改路径 = 改引擎
├── examples/minimal/         # 最小示例：一份任务书 + 期望的裁决形状
└── reports/REDACTION-REPORT.md      # 脱敏报告（哪些文件没公开、为什么）
```

---

## 2. 插件入口契约

```js
import { defineTool } from '@deepseek-ai/dsh-tools';
import Schema from '@deepseek-ai/schemastery';

export const name = 'ai-team';
export const inject = ['tools'];          // MVP 只依赖 tools，别加没用的
export const Config = Schema.object({ ... });
export function apply(ctx, config) {
  ctx.tools.register(defineTool({ ... }));
}
```

**`defineTool` 的形状**（与官方插件同范式）：

```js
defineTool({
  name: 'ai_team_review',                             // 工具名
  description: '...',                                 // 给模型看
  parameters: {                                       // 不写 = 无参
    brief: { type: 'string', description: '...', required: true },
  },
  output: {
    schema: { type: 'object', properties: { ... }, additionalProperties: false },
    render(_args, value) { return [{ type: 'text', text: '...' }]; },
  },
  timeoutMs: 300000,
  async execute(args, exec) { return { ... }; },       // exec.signal 是 AbortSignal
})
```

**三条实测坑**：

- `output.schema` 的 `required` 写在**属性对象内部**（`{ type: 'string', required: true }`），
  不是 JSON Schema 的顶层数组。
- `additionalProperties: false` 必须写，否则输出校验可能放过脏字段。
- `execute` 抛异常 = 工具失败，模型会看到错误文本。
  **「验收官说 rework」不是异常** —— 那是正常结果，必须正常返回。

---

## 3. 五个工具的参数与返回

### 3.1 `ai_team_init`

把团队模板铺进当前工作区。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `target` | string | 否 | 目标目录，默认当前工作目录 |
| `force` | boolean | 否 | 已存在是否覆盖，默认 `false` |

返回：

```js
{ created: string[], skipped: string[], root: string }
```

行为：从包内 `templates/` 复制
`AGENTS.snippet.md` → `<target>/AI-TEAM.md`、
`REVIEW-BRIEF.md` → `<target>/REVIEW-BRIEF.md`、
`INC-TEMPLATE.md` → `<target>/INC-TEMPLATE.md`、
`roles/*` → `<target>/<outDirName>/roles/*`（默认 `<target>/.ai-team/roles/*`）。
已存在且 `force=false` 时跳过并记入 `skipped`。

### 3.2 `ai_team_review`（核心）

送独立验收官，产出三态裁决。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `brief` | string | 是 | 任务书内容，或以 `.md` 结尾时按路径读取 |
| `round` | string | 否 | 轮次，默认 `"1"` |
| `backend` | string | 否 | `codex` / `openai`，默认取 `config.backend` |
| `model` | string | 否 | 覆盖默认模型 |
| `dryRun` | boolean | 否 | 只组装 prompt，不真调 |

返回（**必须通过 `verdict.js` 校验后才返回**）：

```js
{
  verdict: 'pass' | 'rework' | 'escalate',
  confidence: number,                             // 0–1
  blocking: string[],                             // 每条 blocking 必须带证据引用
  findings: [{ id, claim, evidence, type }],      // type ∈ E0..E3 证据强度
  outDir: string,                                 // 裁决落盘目录
  raw: string                                     // 验收官原文（截断到 20000 字符）
}
```

**裁决纪律（要写进工具 `description`，让模型也照做）**：

- 只允许三态，不许「基本可以 / 大致没问题」。
- 每条 `pass` 必须有 `file:line` 或命令输出；引不出 → 该条降为 `rework` blocking。
- 验收官的「只读」**是纪律 + 事后可核，不是沙箱隔离** —— 见 §5。返回值里必须如实标注执法等级。

### 3.3 `ai_team_plan`

需求 → DAG 图。调用 `engine/manager.py`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `brief` | string | 是 | 需求文本或 `.md` 路径 |
| `outDir` | string | 否 | 默认 `<cwd>/.ai-team/runs/<runId>` |
| `model` | string | 否 | 转给经理层 |
| `dryRun` | boolean | 否 | 只查环境与规则漂移，不调后端 |

返回：`{ graphPath, runId, nodeCount, summary }`

### 3.4 `ai_team_gate`

判图。调用 `engine/dag-gate.mjs`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `graph` | string | 是 | `GRAPH.json` 路径 |

返回：`{ ok: boolean, errs: [], warns: [], codes: [] }`

**`ok=false` 不许继续跑** —— 工具必须明确告诉模型「这一步必须停」。
继续跑只会产出没法归因的证据。

### 3.5 `ai_team_run`

执行图。调用 `engine/runner.py`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `graph` | string | 是 | `GRAPH.json` 路径 |
| `engine` | string | 否 | `headless` / `fixture` / `codex`，默认 `fixture`（安全默认：不花钱、不写主树） |
| `runId` | string | 否 | 不填按时间生成 |
| `fixtureDir` | string | 否 | **仅 `engine='fixture'` 生效**：场景目录（含 `spec.json`），透传成 `runner.py --fixture-dir <v>`；相对路径按工作目录解析 |

**`fixtureDir` 的回落顺序**（前者不成立才看后者，只认含 `spec.json` 的目录）：

1. 显式传入的 `fixtureDir`
2. `<cwd>/<outDirName>/fixtures`（默认 `<cwd>/.ai-team/fixtures`，用户自备场景）
3. 随附的 `tasks/DAG-2026-11-06/fixtures/scenarios/honest`

**非 `fixture` 引擎却传了 `fixtureDir` → 明确报错**（不静默吞）。
静默忽略会让调用方以为「场景切换生效了」，而那正是最坏的一类错觉。

返回：`{ runId, result: 'pass' | 'fail', nodes: [{ id, status, seconds }], runDir }`

**`fixture` 已随附，可直接跑通**（此命令为此前实测记录，可复现）：

```powershell
python engine/runner.py tasks/DAG-2026-11-06/fixtures/graphs/g3.json `
  --engine fixture --fixture-dir tasks/DAG-2026-11-06/fixtures/scenarios/honest
```

期望：`gate OK（0 err / 1 warn）` · 3 波 · 3 节点全 `ok` · `result PASS` ·
证据包落 `runs/<id>/`，且**含 `FAKE` 标记文件**（用来防止它被误当真实证据）。

`fixture` 用的是 `tasks/.../fixtures/` 下的假 worker + 场景 spec，**路径不可挪**（见 §1）。

#### 随带的 40 个场景就是现成的回归台

`tasks/DAG-2026-11-06/fixtures/scenarios/` 下是 **40 个场景**（每个一份 `spec.json`，无缺件），
覆盖的正是「不诚实的工人」那几类形态：

| 类 | 场景（40 个全覆盖） |
|---|---|
| 诚实基线（1） | `honest` |
| 谎报 / 少报（3） | `overclaim`（什么都没改却报 changed）· `underclaim`（真改了却交空数组）· `oos`（越界改了还不报，须同时命中 `out_of_scope` + `underclaim`） |
| 越界与「不可洗」（5） | `escape-writes`（写副本外，只记录不进 harm）· `sandbox-writes`（写 TEMP，落证据包内 SANDBOX 豁免区）· `wash`（先写主树再改交 patch：违规算 harm，重试洗不掉）· `retry-oos`（越界不许重试）· `harm-escalate`（harm 当场判死，不重试不升档） |
| 碰证据（5） | `evidence-write`（窗口内写证据包）· `evidence-tamper`（改已登记证据并复原 mtime/size）· `evidence-retamper`（同一文件第二次篡改仍须抓到）· `evidence-cascade`（连坐判据不能栽给没碰东西的同胞）· `parallel-tamper`（并发组里篡改，波级连坐） |
| 重试 / 升档 / 超时（7） | `retrythenok` · `retryreason`（失败原因必须传下去）· `needinfo`（缺信息不猜）· `escalate`（升级不是静默忽略）· `detcmd`（确定性失败只许 1 次）· `cmdfail`（accept 的 cmd 真退出 1）· `timeout` |
| 并行与汇合（10） | `parallel` · `parallel-write`（写节点不许并发）· `parallel-abort` · `parallel-violation` · `parallel-check` · `parallel-join` · `parallel-retry2` · `parallel-budget` · `parallel-two-groups` · `join-any` |
| 条件跳转（3） | `when-yes` · `when-no` · `from`（上游失败对下游的传导） |
| 协议 / 裁决 / 预算（4） | `check-protocol`（没交 verdict）· `check-protocol-bad`（交了个读不懂的词）· `check-verdict`（判 fail 但缺 status）· `budget`（预算不够时拒重试并记原因） |
| 判据细节（2） | `idem`（只改 mtime、内容不变 → 不得算 modified）· `patch`（只交 patch / 直接落盘 / 空 patch / 越界 patch 四种待遇不同） |

**同一张图、同一份代码，只换 `fixtureDir` 就会得到不同裁决** —— 这是最便宜的「看判据真的在判」的方法：
拿 `g3.json` 分别指向 `scenarios/honest` 与 `scenarios/overclaim`，前者应 `pass`、后者应 `fail`
且 `why` 指到具体文件（如 `overclaim=…/sandbox/notes.md`）。
一个从不判 `fail` 的判据等于没有，而这条反证不需要花一分钱模型额度。

> ⚠️ **两条使用前提**（不做就会得到假结论，详见 [`PORTING.md`](PORTING.md) §9）：
> ① **fixture 不幂等** —— 它会改 `fixtures/sandbox/` 下的文件，第 2 次跑同一场景会静默变成假失败；
> ② **必须独占跑** —— 同一时刻只能有一个 runner，否则两个 run 互相把对方判成 `evidence_write`。

---

## 4. Config 契约

挂在 profile patch 的 `config` 下（键名与默认值以此为准）：

```js
export const Config = Schema.object({
  backend: Schema.union([Schema.const('codex'), Schema.const('openai')])
    .default('codex').description('验收官后端'),
  model: Schema.string().default('').description('默认模型，留空用后端默认'),
  codexPath: Schema.string().default('codex').description('codex CLI 可执行文件路径'),
  openaiBaseUrl: Schema.string().default('https://api.openai.com/v1').description('OpenAI 兼容 API 基址'),
  openaiApiKeyEnv: Schema.string().default('OPENAI_API_KEY')
    .description('存放 API Key 的**环境变量名**（不要把 key 写进配置）'),
  timeoutMs: Schema.number().default(3600000).description('单次验收超时'),
  engineDir: Schema.string().default('').description('引擎目录，留空用包内 engine/'),
  pythonPath: Schema.string().default('python').description('Python 可执行文件'),
  nodePath: Schema.string().default('node').description('Node 可执行文件'),
  outDirName: Schema.string().default('.ai-team').description('运行产物目录名'),
  codexSandbox: Schema.string().default('workspace-write')
    .description('codex 后端的沙箱档（见 §5）；read-only 会让验收官连读文件都做不到'),
});
```

> **`codexSandbox` 的来由**：它由 §5（adapter 契约）引入，原清单里没有。
> 若你改动实现，`src/index.js` 的 `Config` 与 `cordis.patch.yml` 两处必须同时改，
> 否则用户改配置会静默不生效。

**安全红线**：`openaiApiKeyEnv` 只存**环境变量名**，绝不接受 key 字面量；
日志与返回值中不得出现 key。

---

## 5. 后端 adapter 契约

```js
// 每个 adapter 导出：
// async function invoke({ brief, model, timeoutMs, signal, readOnly, cwd })
//   -> { text, usage?, meta }
```

### 5.1 `codex-cli.js`

- 调 `codex exec --json`，prompt 走 stdin。
- **沙箱档可配置**（Config `codexSandbox`，默认 **`workspace-write`**）。

> ⚠️ **为什么默认不是 `read-only`（实测更正）**：
> Windows 上 `--sandbox read-only` 实际是「**禁止执行任何命令**」，连 `Get-Content` 都被
> `blocked by policy` 挡掉。验收官会因此读不到文件、跑不了命令，只剩「判任务书叙述层」——
> 那样验收等于没做。
>
> 所以本插件的口径是：**只读是纪律 + 事后可核，不是沙箱隔离。**
> 默认给能干活的权限，越界靠前后快照（或 `git status`）发现，
> 并在返回值里**如实标注执法等级**（`enforcement: 'snapshot_only'`）。
> **不许把「事后可核」说成「隔离」** —— 那是两件事。
>
> 依据：`engine/runner.py` 的 codex 引擎自记（「只读执法改由快照核承担」），
> 以及 `engine/review-codex.py` 的 `readonly_enforcement: snapshot_only` 约定。

- **超时必须杀进程树**：Windows 上孙进程会残留，Node 侧用 `taskkill /T` 兜底。
  ⚠️ **`engine/` 里的 Python 侧并没有用 `taskkill`**（全仓 0 命中）——
  它靠 **Windows Job Object**（`engine/review-snapshot.py:136/140/147-151/173/176`，
  且 `:173` 对非 Windows 直接 `raise`），另两处只有 `p.kill()`、不杀孙进程
  （`engine/fire-chief.py:635-646`）。**两套机制不通用，别以为 engine 里有现成的可复用。**

### 5.2 `openai-compat.js`

- `POST ${openaiBaseUrl}/chat/completions`。
- 纯文本后端**没有文件读写能力**，所以 **prompt 里必须注入工作区快照**
  （只读工具能读到的内容由调用方提供）。
  ⚠️ MVP 阶段允许只给 `brief` —— **这个限制必须写进文档与工具 `description`**，
  不许让用户以为纯文本后端也能「自己去看文件」。
- 执法等级如实标注为「无能力」，不是「只读」。

### 5.3 失败语义

| 情形 | 处理 |
|---|---|
| 后端不可用（CLI 缺失、鉴权失败、连不上） | **抛 `Error`** → 工具失败，模型可见 |
| 后端可用但裁决不合规 | 走 §6 的降级（默认 `escalate`，**绝不默认 `pass`**） |
| `dryRun` | 只组装 prompt，**绝不产出 `pass`**；无后端时落到 `escalate` 或明确报错 |

---

## 6. 三态裁决解析器 `verdict.js`

必须导出三个函数：

```js
export function parseVerdict(text)         // -> { verdict, confidence, blocking, findings } | null
export function normalizeVerdict(parsed)   // 不合规时降级：无 verdict → escalate；pass 但 blocking 非空 → rework
export function assertVerdict(v)           // 抛错，用于最后一道闸
```

**四条硬规则**：

1. 解析失败 / 无 `verdict` 字段 → **`escalate`**（不是 `fail`，更不是 `pass`）。
2. `verdict === 'pass'` 且 `blocking.length > 0` → **强制改 `rework`**（自相矛盾）。
3. `findings` 每条 `evidence` 为空 → 该条标 `type: 'E0'`，并在 `blocking` 里追加一句「证据缺失」。
4. **绝不把模型原文里的 "pass" 字样当裁决** —— 只认结构化字段。

> 这四条是**降级器**，不是校验器：不合规时结果往下走（`escalate`），绝不往上走（`pass`）。

---

## 7. 验收标准（自检清单）

一个实现要算合格，下面九条都得有证据：

1. `node --check` 全部 `src/**/*.js` 通过。
2. 插件能被 `import`，且导出 `name` / `inject` / `Config` / `apply` 四个符号。
3. `verdict.js` 有可跑的断言机（`node src/lib/verdict.test.mjs`），至少覆盖：
   正常三态、缺字段、`pass` + `blocking`、证据为空。
4. 每个工具都有 `description` + `parameters` + `output.schema`，且 `additionalProperties: false`。
5. 仓库内**零敏感残留**：本机用户名、VPS IP、代理端口、以及 `dsh` / `gho` 开头的 token 前缀
   （形如 `前缀_…`）一律搜不到。判据用模式匹配，不靠人眼。
6. README 的「5 分钟上手」步骤能照做（安装命令、依赖来源、配置示例、第一个工具调用）。
7. `package.json` 的 `dsh.bundle.patch` 指向真实存在的 `cordis.patch.yml`。
8. **`ai_team_run` 的 `fixture` 链路真能跑通**（不许只写「支持」）：
   跑 §3.5 那条命令，必须拿到 `result PASS` 且 `runs/<id>/FAKE` 存在。
9. **`ai_team_review` 的 dry-run 绝不产出 `pass`**：
   无后端时要落到 `escalate`（或明确报错），**不许伪造裁决**。

---

## 8. 诚实边界

这一节是契约的一部分，**必须原样进入 README**，不许美化。

1. **引擎（`engine/`）是「实战原样」，不是「跨平台重写」。**
   绑定 Windows + PowerShell + 特定后端（codex CLI / 本地网关）的部分**一行都没重写**。
   逐条清单见 [`PORTING.md`](PORTING.md)。
2. **`fixture` 引擎是假 worker。**
   它**能跑**（夹具已随附，§3.5 有可复现命令），但它只验**流程**、不验**产物**，也不调任何模型。
   `headless` 才真调模型。`fixture` 跑出来的 `result=pass` **不能当交付证据**。
3. **`result=pass` 只代表流程跑通，产物对不对要另跑 `verify-product`。**
   原工程每个真跑单元都有一份 `verify-product.py` 判产物 —— **本仓库未随附**，得自己写。
4. **验收官仍可能顺从（LLM 通病）。**
   本仓库的对策是**契约约束 + 证据强制**（三态只许三选一、每条 `pass` 必须带证据、
   引不出就降级、证据按强度分档、闸门机械核）。
   这些**抬高糊弄成本**，但**不承诺 100%**。
