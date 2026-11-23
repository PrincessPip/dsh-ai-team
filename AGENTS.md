# dsh-ai-team · 项目技术档案

> 本文件写给所有接手者（人和 AI 编码代理）：**只写技术事实与已踩过的坑**，不写偏好、不写流程口号。
> 契约（`docs/CONTRACT.md`）与它冲突时以契约为准；本文件负责「为什么这么写、哪里有雷」。

---

## 一句话

**官方 agent-team 管「谁来干」，本项目管「干得对不对」。**
独立验收官 + 三态裁决（pass/rework/escalate）+ 无证据不算通过 + DAG 编排 + 闸门链。两者互补，可同装。

仓库：`github.com/PrincessPip/dsh-ai-team` · MIT · 当前版本 `0.2.0`（2026-11-24；`0.1.0` 同日首发）

---

## 目录职责

| 路径 | 是什么 | 谁该改 |
|---|---|---|
| `src/` | DSH 插件本体：5 个工具 + 3 个后端 adapter + 裁决解析器；`swarm.js`（蜂群提示词与开关读取）、`client.js`（浏览器半边：输入框开关） | 改插件行为 |
| `engine/` | 实战引擎**原样迁移**：`manager.py`（出图）· `dag.workflow.mjs` + `dag-gate.mjs`（Gate 54 条判据）· `runner.py`（执行器）· 闸门链 · `fire-chief.py` | 谨慎：这是别人生产工程的原样，改动会与上游脱节 |
| `tasks/DAG-2026-11-06/fixtures/` | 40 个回归场景 + 假 worker（90 文件 / 240 KB） | 只有加场景时 |
| `docs/` | 架构 / 移植指南 / 验收契约 / 触发策略 / 116 份真实裁决 | 文档 |
| `templates/` | 团队模板：角色卡、任务书、事故单 | 提供 `ai_team_init` 铺出的内容 |
| `reports/` | 脱敏报告（规则 + 逐文件命中）· 首轮独立验收裁决 | 过程记录，一般不改 |
| `tasks/SWARM-MODE-2026-11-24/` | 蜂群模式：设计稿 + 交付记录 + 证据 + 装配探针 | 见该目录 `DELIVERY.md` |

**契约唯一事实来源**：`docs/CONTRACT.md`。`src/` 与它不一致时，以契约先对齐（或改契约并说明理由），不要在代码里默默偏离。

---

## 关键事实（都已实测，不要再猜）

### 插件范式
- 入口形状：`export { Config, apply, inject, name }`；`name = 'ai-team'`，`inject = ['tools']`
- 工具用 `defineTool({ name, description, parameters, output, execute })` 注册，`ctx.tools.register(...)`
- `output.schema` 的 `required` 写在**属性对象内部**（`{ type:'string', required:true }`），不是顶层数组；必须写 `additionalProperties: false`
- 可照抄的样板：`<DSH_HOME>/profiles/<profile>/node_modules/dsh-gsv-tts/lib/index.js`（第三方工具插件）、官方 `@deepseek-ai/dsh-experimental-tool-agent-team/lib/index.js`（`jsonOutput` 写法）

### 浏览器半边（client 插件）与蜂群模式
- `package.json` 的 `dsh.client`（`inject` 官方 client 包 + `platform: web`）+ `exports["./client"]` 指向 `src/client.js`；
  后者是**手写 ModuleLoader bundle**（`window.__ModuleLoader__.load({ id, factory })`），不构建、无 JSX
- 状态通道：Host 侧 `Config` 字段加 `.volatile()` → 客户端 `ctx.configForms.get(<插件行 id>)` 读 `getSnapshot()`、写 `set(field, value)`。
  ⚠️ **没有 `settingsScope` 这个服务**：`dsh-workspace-write-plus/src/client.js` 里那套在本机 DSH 本体里取不到，照抄会永远「读取中」
- 提示词注入：`ctx.inject(['systemPrompt'], …)` + `systemPrompt.section({ name, order, text: () => … })`，`text` **每个模型步重读**。
  section 顺序表在 `dsh-system-prompt` 的 `SECTION_ORDERS`（`TEAM_POLICY=600`、`PTC_ONLY=800`；蜂群取 **700**）
- UI 槽位：**先看 `kind` 再选槽**。`conversation.input.permission` / `.plan` / `.model` / `.attachments` 都是 `single`（单占位，只渲染第一个 occupant），
  加第二个控件会顶掉官方控件；`list` 型（`conversation.input.left` / `.right` / `.dock` / `.overlay` / `composer.dock`）允许多人共存，
  按 `order` **升序**渲染、缺省 `0`。详见「已知坑 12」。
- 品牌蓝只能用静态色 `--dsw-static-deepseek-450 (#5686fe)` / `-500 (#4176e6)`；
  `--dsw-alias-brand-primary` 实测是中性黑/白（浅色 `bluish-1000`、暗色 `bluish-50`），**不是蓝**
- 零模型验证：`node tasks/SWARM-MODE-2026-11-24/probe-prompt.mjs`（把 `dsh-system-prompt` + 本插件挂进最小 cordis，直接看 `assemble()` 的渲染结果）

### 引擎调用
| 工具 | 实际命令 | 退出码语义 |
|---|---|---|
| `ai_team_plan` | `python engine/manager.py <BRIEF.md> [--run-id] [--model] [--dry-run]` | 0 成功 |
| `ai_team_gate` | `node engine/dag-gate.mjs <GRAPH.json> [--json]` | 0 通过 / 1 拒收 / 2 读不到 |
| `ai_team_run` | `python engine/runner.py <GRAPH.json> --engine fixture\|headless\|codex [--fixture-dir]` | `result` 以 `RUN.json` 为准，不看退出码 |

### fixture 怎么跑（唯一验证过的「开箱即跑」路径）
```bash
python engine/runner.py tasks/DAG-2026-11-06/fixtures/graphs/g3.json \
  --engine fixture --fixture-dir tasks/DAG-2026-11-06/fixtures/scenarios/honest
```
期望：`gate OK(0 err/1 warn)` → 3 波 → `s_recon/w_write/k_check` 全 ok → `result PASS` → `runs/<id>/FAKE` 存在（该标记用于防止 fixture 产物被误当真实证据）。

---

## 已知坑（踩过的，别重踩）

1. **`engine/dag.workflow.mjs` 和 `selfcheck.workflow.mjs` 是 workflow 脚本**（顶层 `return`），`node --check` 会报 "Illegal return statement" —— **这是正常的**，别去"修"。
2. **fixture 不幂等**：`honest` 会把 `sandbox/notes.md` 从 3 条写成 4 条，第二次跑同样内容 = 零改动 → 被判 `overclaim` **假失败**，失败字符串与真 overclaim 一模一样。**跑前先备份 / 还原 `tasks/DAG-2026-11-06/fixtures/sandbox/`**（整目录，不是四个文件——`wash` 会新建 `PATCH-A.md`）。
3. **fixture 必须独占跑**：并发时别的进程往 `runs/` 写会被判 `checks.evidence_write`（不可洗 harm，一命中就 abort）。**同一时刻只能有一个 runner。**
4. **仓库根若有指向 DSH 的 `node_modules` junction，`runner.py` 的 `snapshot()` 会跟进整棵树**（实测 6.9 万条 / 2.06 s，放大 ~157 倍；`rglob` 不跳过 junction，只跳过真 symlink）。跑夹具前把 junction 挪出仓库。
5. **`engine/` 里没有 `taskkill`**：Python 侧靠 Windows Job Object（`review-snapshot.py:136-176`，`:173` 对非 Windows 直接 `raise`），另两处只有 `p.kill()` 不杀孙进程。Node 侧（`src/lib/proc.js`）自己实现 `taskkill /T /F /PID`。
6. **peer 依赖的 prerelease 坑**：`^0.2.0-rc.2` **不匹配** `0.2.1-rc.2`；`>=0.2.0-0`、`0.2.x`、连 `*` 也都不匹配。唯一可靠写法是**逐个 tuple 显式枚举**（现为 `^0.2.0-rc.2 || ^0.2.1-rc.2 || ^0.2.2-rc.5 || ^0.2.3-alpha.2`）+ `peerDependenciesMeta.optional`。
7. **`tasks/` 必须在 `package.json` 的 `files` 里** —— 否则 registry 安装拿不到夹具，默认 `engine='fixture'` 直接失效。
8. **「脱敏零残留」≠「代码还成立」**：曾经脱敏把 `fire-chief.py` 的默认端口换成非法占位符，扫器报干净但代码已坏。改脱敏产物后要跑一次真实调用。
9. **`volatile` 字段运行时是引用不是值**：`Config` 里 `.volatile()` 的字段在 `apply(ctx, config)` 拿到的是 `{ get() }`（`@deepseek-ai/cosmokit` 的 `Volatile`），**不能当布尔用、也没有 `valueOf`**；`{ ...DEFAULTS, ...config }` 展开会把引用固定成那一刻的值，之后再热改就看不见（蜂群开关必须读原始 `config.swarmMode.get()`）。
10. **装进 profile 别用 `dsh plugin add`**：2026-11-23 实测会重解析依赖、把 `@openai/codex` 从 0.167.2 降回 0.153.4。手工挂法是 junction（`node_modules/<包>` → 本仓库）+ `dsh.profile.bundles` + `cordis.patch.yml` 条目；**junction 不在 lockfile 里，谁跑 `pnpm install` 都会被清掉**。
11. **system prompt 是每个模型步重组装，不是每轮**：改 volatile 配置后下一次工具调用/下一次生成就生效，**不需要重启 `dsh web`、不需要新开会话**；只有 client 半边要刷新浏览器页面才取到新模块。
12. **插槽有 `kind`，`single` 型不能挂第二个控件**：`conversation.input.permission` / `.plan` / `.model` / `.attachments` / `.activity` / `composer.bar` 都是 `kind: 'single'`（契约在 `dsh-client-ui-conversation/lib/types/client/contract/slots.d.ts`），渲染器对 single 只取 `entriesOfSlot(key)[0]` —— 注册进去会**把官方控件整颗顶掉**（蜂群开关首版就踩了：权限选择器消失）。要加控件请挑 `kind: 'list'` 的槽（输入框附近：`conversation.input.left` / `.right` / `.dock` / `.overlay` / `composer.dock`），其中 `.left` 紧随 `div.modes` 之后渲染、视觉上就在权限控件右边。**要挤到最前面得用负 order**：`dsh-voice-local` 占着 `-100`，蜂群开关取 `-200` 才落在「权限」与「语音」之间；list 槽里 order 更小者只是往右挪，不挤人。
13. **profile patch 里的覆盖条目会被 settings 写入「按需删除」**：`ConfigEditor.edit()` 在「下一份 config 深度等于继承值」时删掉 `config` 键，条目只剩 `id+name` 就整条删（`dsh-config-editor/lib/index.js:99-104`，AST 编辑、保留别处的注释）。所以 `swarmMode: false`（= bundle 默认）时 patch 里没有 `dsh-ai-team` 是**正确状态**，点 ON 才会出现 `swarmMode: true`。⇒ 别把说明注释写在该条目旁边（会跟着消失），也别手工把它补回去。

---

## 测试与验收

- 语法：`node --check` 覆盖 `src/**/*.js`（**不含** engine 下两个 workflow 脚本）
- 断言机：`node src/lib/verdict.test.mjs` → 必须 `PASS 23/23`；`node src/swarm.test.mjs` → 必须 `PASS 15/15`
  （`npm test` 两个都跑；`npm run check` 三个入口文件的语法）
- 装配探针：`node tasks/SWARM-MODE-2026-11-24/probe-prompt.mjs` → `PASS 全部符合预期`（零模型调用，只看 system prompt 渲染结果）；
  `node tasks/SWARM-MODE-2026-11-24/probe-client.mjs` → `PASS 11 / 11`（伪造 ModuleLoader/React/ctx，验浏览器半边的槽位注册形状）
- 导出形状：`node -e "import('./src/index.js').then(m=>console.log(Object.keys(m)))"` → 应有 `Config/apply/inject/name`（多导出 `DEFAULTS` 属超集，不扣分）
- 脱敏闸门：`.github/workflows/ci.yml` 的 `redaction` job（**注意它的正则自身会命中那些模式，判它时要区分"规则"与"泄露"**）
- 独立验收 → 裁决落 `reports/VERDICT-round1.json`。**首轮结论是 `rework / 0.90 / 3 blocking`，三条已修**；下次改完应出新的一轮。

---

## 当前状态（2026-11-24）

- `0.1.0` → `0.2.0`：蜂群模式（`src/swarm.js` + `src/client.js` + `src/swarm.test.mjs`）已落地并装入生产 profile，
  UI 人工验收通过（权限控件与开关共存、两态点击、暗色主题、位置 `权限 → 蜂群 → 语音`）。
- **仍未验的一条**：开启后让 Lead 做一件实现类小事，观察它是否先调 `ai_team_plan`。
  探针只能证明「文本进了 system prompt」，证明不了「行为被改变」。
- 发布件：`README.md`（中文）/ `README.en.md`（英文）/ `ROADMAP.md`（中英双语）/ `CHANGELOG.md`。
- 生产挂载示例（作者本机）：`profiles/web` 里用 junction + `dsh.profile.bundles` + `cordis.patch.yml` 条目，
  **不走 `dsh plugin add`**（见「已知坑 10」）。

---

## 不在仓库里 / 不要假装有

- **`verify-product`**：原工程里每个真跑单元配一个，用来判**产物**对不对。本仓库没有随附 → `result=pass` 只代表流程跑通
- **`runner-harness` / `manager-harness` / `tier-drift`**：回归台没随附。改 `runner.py` / `manager.py` 之前先想清楚怎么验
- **`codex-gw` 网关 / `*-egress-check.ps1`**：`engine/review-codex.py` 与 `fire-chief.py` 依赖它们，5 处硬引用。移植见 `docs/PORTING.md`
- **原工程的 `tasks/` 大树**（4.6 GB）：只迁了 `DAG-2026-11-06/fixtures/`
