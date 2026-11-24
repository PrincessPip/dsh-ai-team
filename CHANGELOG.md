# Changelog

本文件记录对外可见的变更。格式参照 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [语义化版本](https://semver.org/)。

---

## [0.2.1] — 2026-11-24

本版为文档与已知坑更新版：**包行为无变化**（`src/`、`engine/`、工具与裁决逻辑均未改动，升级后行为与 `0.2.0` 一致；已装用户无需重装、无需改 profile）。

### 新增

- **`templates/LEAD-PLAYBOOK.md`** —— Lead 派活清单：哪些形状的活必须先交给工人 / 验收官，
  以及不派时必须怎么留痕（蜂群例外通道）。

### 变更

- **`README.md`**（`README.en.md` 同步）—— 安装方式改用 GitHub tag 安装：
  `dsh plugin --profile web add github:PrincessPip/dsh-ai-team#v0.2.1`，
  不再以 npm registry 发布为前提（包名在 npm 上已被同类竞品占用）。
- **`AGENTS.md`** —— 已知坑与当前状态更新：补记「蜂群把出图绑死在 codex 隧道上」（坑 14），
  以及 npm 发布路线放弃、改用 git tag + GitHub Release 发布。

---

## [0.2.0] — 2026-11-24

### 新增

- **蜂群模式（swarm mode）** —— 输入框里的 `蜂群 ON` / `蜂群 OFF` 两态开关。
  开启后 Lead 从「工人」降为「调度员」：实现类任务必须先 `ai_team_plan` 出图 → `ai_team_gate` 判据 → `ai_team_run` 执行；
  只读操作不受限；不走编排时必须在回复开头写 `[蜂群例外: 理由]`。关闭即回原生行为。
  - `Config.swarmMode`：**volatile** 字段，客户端经 `configForms` 热写，loader 只提交值、不重挂插件。
  - `src/swarm.js`：提示词正文与开关读取的唯一真值（`ai-team:swarm-mode` section，order 700）。
  - `src/client.js`：浏览器半边。注册在 composer 的 `conversation.input.left`（`kind: 'list'`）槽，
    `order: -200`，因此位置是 `权限控件 → 蜂群开关 → 语音输入`。
  - 注入的 section 由 `systemPrompt.section({ text: () => … })` 提供，**每个模型步重新求值**：
    切换开关后下一个模型步即生效，**不需要重启 `dsh web`、不需要重开会话**（只有浏览器半边需要刷新页面）。
- **英文文档** —— `README.en.md`（README 全文英文版）与 `ROADMAP.md`（中英双语路线图）。
- **测试** —— `src/swarm.test.mjs`（15 条断言：开关读取三形状 / 提示词要素 / Config volatile / apply 装配 / 每轮重读）。
- **验证探针**（零模型调用，可复跑）—— `tasks/SWARM-MODE-2026-11-24/probe-prompt.mjs`（直接看 system prompt 装配结果）、
  `probe-client.mjs`（伪造 ModuleLoader / React / ctx，验浏览器半边的槽位注册形状）。
- **设计稿与交付记录** —— `tasks/SWARM-MODE-2026-11-24/`：设计决策、实测修正、证据文件、回滚步骤。

### 变更

- `package.json`：新增 `./client` 导出与 `dsh.client`（`platform: web`）声明；`check` / `test` 脚本覆盖新文件。
- `cordis.patch.yml`：新增 `swarmMode: false`。

### 已知限制

- 开关是**全局**的：一次切换影响所有会话（会话级开关见 [`ROADMAP.md`](ROADMAP.md) 计划中）。
- 纯提示词约束可能被模型忽略 —— 这正是例外通道存在的理由；事后核查看回复里有没有 `[蜂群例外: …]`。
- 客户端读写的入口 id 必须与 profile patch 里的插件行 id 逐字一致（都是 `dsh-ai-team`），否则开关不出现或禁用。

---

## [0.1.0] — 2026-11-24

首个公开版本。

### 新增

- 五个工具：`ai_team_init` / `ai_team_review` / `ai_team_plan` / `ai_team_gate` / `ai_team_run`。
- 三态裁决（`pass` / `rework` / `escalate`）+ 证据强度 `E0`–`E3` + 无证据不算通过（由闸门与解析器机械执行）。
- 引擎原样迁移（`engine/`）：经理层、Gate、执行器、闸门链、救火执行器。
- 40 个回归场景 + 假 worker（`tasks/DAG-2026-11-06/fixtures/`）。
- 契约与判据文档（`docs/`）+ 116 份脱敏后的真实裁决。
- 团队模板（`templates/`）与最小示例（`examples/minimal/`）。
