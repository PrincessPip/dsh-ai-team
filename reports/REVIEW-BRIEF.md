# REVIEW-BRIEF · dsh-ai-team 开源首版验收

> 你是**独立验收官**。你的工作是核验，不是帮忙。**不要修任何文件**，只读、只跑只读命令。
> 先自己核，再看别人的自述；二者冲突时，以你自己能复现的为准。

仓库根：本仓库工作区根目录（下称 `<REPO>`）。所有命令都在该目录下执行，路径一律用相对路径。

---

## 一、判据（逐条给 `pass` / `fail` / `unverified` + 证据）

证据要求：`file:line`、命令原文+退出码、或产物路径。**引不出证据的条目一律不算 pass**。

### C1 插件可导入、导出形状正确
- 期望导出 `name` / `inject` / `Config` / `apply`（多导出不扣分，少一个就 fail）
- `name` 应为 `'ai-team'`

### C2 五个工具的形状与契约一致
- 工具名必须是：`ai_team_init` / `ai_team_review` / `ai_team_plan` / `ai_team_gate` / `ai_team_run`
- 逐个核对：`description` 非空、`parameters` 与契约的参数表一致（名字/类型/必填）、`output.schema.type === 'object'`、`additionalProperties: false`
- 契约在 `docs/CONTRACT.md`
- **参数表是最容易出错的地方，逐个对，别抽样**

### C3 裁决解析器断言机全绿
- `node src/lib/verdict.test.mjs` 必须全过且退出码 0
- 额外核：断言机是否覆盖了「pass + blocking 非空 → 强制 rework」「无 verdict → escalate」「evidence 为空 → E0」

### C4 fixture 链路真能跑通（**这条最重要**）
- 跑：`python engine/runner.py tasks/DAG-2026-11-06/fixtures/graphs/g3.json --engine fixture --fixture-dir tasks/DAG-2026-11-06/fixtures/scenarios/honest`
- 期望：`result PASS`，且 `runs/<runId>/FAKE` 文件存在（该标记用于防止 fixture 产物被误当真实证据）
- ⚠️ 这条会真写 `tasks/.../fixtures/sandbox/` 下的文件。**跑之前先备份该目录，跑完还原**，别把工作树弄脏。
- 若跑不通，`unverified` 或 `fail` 均可，但**必须贴失败输出**

### C5 脱敏零残留（**安全判据，不许含糊**）
在仓库内搜（排除 `.git`、`node_modules`）：
- 本机用户名、Windows 绝对用户路径（形如 `C:\Users\<名字>`）
- VPS / 公网 IP、私网网段
- 代理端口号、私人工具名
- 任何 token 字样（`ghp_` / `gho_` / `sk-` / `github_pat_`）
- **注意**：`.github/workflows/ci.yml` 里的检测正则本身会命中这些模式，那是规则不是泄露，判它时要区分开。
- 有任何一处真实残留 → 本条 `fail`，并把 `文件:行` 贴出来

### C6 README 的上手步骤能照做
- 逐条读「安装 / 最小配置 / 第一次调用」三步，判断一个新用户是否能照着走通
- 重点核：依赖怎么拿（npm 上 `@deepseek-ai/dsh-tools` 版本是否满足 `package.json` 的区间）、装完是否需要新开会话、配置写在哪
- 有含糊或走不通的步骤 → 指出具体哪一步

### C7 装配一致性
- `package.json` 的 `dsh.bundle.patch` 指向的文件真实存在
- `cordis.patch.yml` 里 `id` / `name` 与包名一致，且 `config` 的键与 `Config` 定义的键一致（多键或少键都要指出）

### C8 「诚实边界」确实存在且没被美化
- 定位 README 里讲局限的那一节，判断它是否如实覆盖了以下四条：
  1. `engine/` 是实战原样、没做跨平台重写
  2. `fixture` 是假 worker，只验流程不验产物
  3. `result=pass` 只代表流程，判产物的尺子（`verify-product`）未随附
  4. 验收官仍可能顺从，不承诺 100%
- **另外核**：README 有没有出现与事实相反的自夸（例如声称"完全隔离""100% 可靠""开箱即用无需依赖"）

### C9 `ai_team_review` 的 dry-run 不会伪造裁决
- 在不配置真实后端的情况下跑 dry-run，结果**不许是 `pass`**
- 落 `escalate` 或明确报错都算合规

---

## 二、额外核验（不计入判据，但请报告）

- N1 `engine/dag-gate.mjs` 喂一张非法图，是否 `ok:false` 且给出 `code`；喂坏 JSON 是否退出码 2
- N2 `engine/dag.workflow.mjs` / `engine/selfcheck.workflow.mjs` 用 `node --check` 会报 "Illegal return statement" —— **这是正常的**（它们是 workflow 脚本，顶层 return）。如果文档里没说明这一点，提出来。
- N3 `docs/PORTING.md` 里给的 `file:line` 引用随机抽 5 条，核对是否真的存在且内容对得上。

---

## 三、输出格式（必须是合法 JSON，不要只写自然语言）

```json
{
  "verdict": "pass | rework | escalate",
  "confidence": 0.0,
  "criteria": [
    {"id": "C1", "status": "pass|fail|unverified", "evidence": "具体证据"}
  ],
  "blocking": [
    {"id": "B1", "what": "问题是什么", "why": "为什么阻塞", "evidence": "file:line 或命令输出"}
  ],
  "nonblocking": [],
  "reviewer_notes": "你核验过程的诚实交代：哪些没核、哪些核不了、有没有先看了别人的自述"
}
```

**三态规则**（不可协商）：
- `pass`：每条判据都有你可复现的证据，且 `blocking` 为空
- `rework`：问题能在现有设计内修（改代码/补测试/补文档），不涉及方向取舍
- `escalate`：需要人做取舍，或你置信度低，或证据不足以下结论
- **不许写「基本可以」「大致没问题」**。说不清就写 `unverified`，不要凑。
