# 蜂群模式（Swarm Mode）· 交付记录

> 单元：`tasks/SWARM-MODE-2026-11-24/`　设计稿：`DESIGN.md`　实施日：2026-11-24
> 结论：**服务端注入链路已机器验证通过；UI 控件已落地但待人工验收（刷新页面 + 两套主题）。**
> 边界：本单元**不涉及** `decide` 节点、不改 Gate、不改引擎（见 `DESIGN.md` §八）。

---

## 一、做了什么

在 `dsh-ai-team` 插件里加了一个全局两态开关 **`蜂群 ON` / `蜂群 OFF`**（输入框权限控件右边），
开启后 Lead 被降为调度员：实现类任务必须走 `ai_team_plan → ai_team_gate → ai_team_run`，
只读操作不受限，偏离必须在回复开头写 `[蜂群例外: 理由]`；关闭即回原生行为。

| 文件 | 改动 |
|---|---|
| `src/swarm.js` | **新增**。提示词正文、section 名/序号、`readSwarmMode()`、`swarmSectionText()` —— 唯一真值 |
| `src/index.js` | `DEFAULTS.swarmMode = false`；`Config.swarmMode = Schema.boolean().default(false).volatile()`；`apply()` 里按需注入 `ctx.inject(['systemPrompt'], …)` 注册一段**每轮重读**的 section |
| `src/client.js` | **新增**。手写 ModuleLoader bundle：**`conversation.input.left`**（list 型）槽的开关，`configForms` 读写 `swarmMode`。⚠️ 最初挂在 `conversation.input.permission`，那是 `kind: 'single'` 单占位槽，会顶掉官方权限控件 —— 见 §四·0 |
| `package.json` | 加 `./client` 导出、`dsh.client`（inject 四个官方 client 包、`platform: web`）；`check`/`test` 脚本覆盖新文件 |
| `cordis.patch.yml` | `swarmMode: false` |
| `src/swarm.test.mjs` | **新增**。15 条断言（开关读取三形状 / 正文 / Config volatile / apply 装配 / 每轮重读） |
| `tasks/…/probe-prompt.mjs` | **新增**。零模型调用的装配探针：把 `dsh-system-prompt` + 本插件挂进最小 cordis，直接 `assemble()` 看渲染结果 |

装上生产 profile 的做法（**不走 `dsh plugin add`**，原因见 §五）：

1. `profiles/web/node_modules/dsh-ai-team` 建 junction 指向本仓库；
2. `profiles/web/package.json` 的 `dsh.profile.bundles` 追加 `dsh-ai-team`；
3. ~~`profiles/web/cordis.patch.yml` 追加 `- id: dsh-ai-team` 覆盖条目~~ —— **这一步不要手工写**，见 §四·1。
   开关值本身会由 `configEditor` 在第一次「值与继承不同」时自动写进 profile patch；值回到继承值（= bundle 层默认 `false`）
   时它会**把 config 键删掉，条目只剩 id+name 就整条删掉**（`dsh-config-editor/lib/index.js:99-104`，AST 编辑、保留其它注释）。
   所以这个条目在 patch 里的**出现/消失都是正常的**：点 ON 出现 `swarmMode: true`，点回 OFF 整条消失。
   手工写在它旁边的注释会跟着条目一起没 —— 说明文档请写在这里，不要写在 patch 里。

---

## 二、证据（可复跑）

| 证据 | 命令 | 结果 | 文件 |
|---|---|---|---|
| 语法 | `npm run check` | 三个文件全过 | `EVIDENCE-tests.txt` |
| 新断言机 | `node src/swarm.test.mjs` | **PASS 15 / 15** | `EVIDENCE-tests.txt` |
| 旧断言机未回归 | `node src/lib/verdict.test.mjs` | **PASS 23 / 23** | `EVIDENCE-tests.txt` |
| **提示词真进 system prompt** | `node tasks/SWARM-MODE-2026-11-24/probe-prompt.mjs` | **PASS**：开 396 字符/4 段含蜂群段；关 48 字符只有身份段；两态差值 348 = 正文 346 + 连接符 2 | `EVIDENCE-prompt-probe.txt` |
| **浏览器半边能挂上槽位** | `node tasks/SWARM-MODE-2026-11-24/probe-client.mjs` | **PASS 11 / 11**：模块加载 → `apply` 跑完 → 注册到 `conversation.input.left`（list 型）、**且不在任何 single 型槽里**、`order=100>0`、`configForms.get('dsh-ai-team')`、locale 两态、样式只用语义/静态变量（无硬编码白底） | `EVIDENCE-client-probe.txt` |
| 生产 profile 组合合法 | `dsh --profile web --dump-config` | `exit=0`，含 `- id: dsh-ai-team` | `EVIDENCE-web-install.txt` |
| 插件真的在跑 | 会话工具表 | 5 个 `ai_team_*` 工具出现在工具表 | 本会话可见 |
| **运行中热切换** | 手改 `cordis.patch.yml` 的 `swarmMode` | 改 `true` → **下一个模型步**系统提示即出现「## 蜂群模式 · 已开启」；改回 `false` → 同一步内消失。**未重启 `dsh web`、未重开会话** | 本会话观察（E1，非机器留存） |

> 证据等级按母体口径：`EVIDENCE-*.txt` 与探针属**机器可复跑**；「热切换」那行是**人观察**，
> 记录的是现象（系统提示两态），不是文件。要升级成机器证据，需要 session 日志留全量 prompt（当前不落盘）。

---

## 三、`DESIGN.md` 六条验收的对账

| # | 验收项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 开关在权限控件右边，两态 `蜂群 ON`/`蜂群 OFF` | **待人工（形状已机验）** | 落点 `conversation.input.left`（list 型，渲染在 `div.modes` 之后）；槽位注册形状由 `probe-client.mjs` 11/11 验过；实际观感需刷新页面看 |
| 2 | 浅色 OFF 白底黑字观感；暗色不出白斑 | **待人工** | OFF = `transparent` + `--dsw-alias-border-l4` + `--dsw-alias-label-secondary`（两主题各自成立） |
| 3 | 开启后新开一轮做小事，观察是否调 `ai_team_plan` | **待人工** | 需一次真实实现类任务；这是唯一能证明「注入改变行为」的验收，探针只证明「文本进了 prompt」 |
| 4 | 关闭后恢复直接动手 | **待人工** | 同 3 |
| 5 | 开启态不走编排必须出现 `[蜂群例外: …]` | **已出现一次** | 本次实施中的热切换复位动作，按要求写了例外声明 |
| 6 | 颜色不遮暗色主题，覆盖 light/dark | **待人工** | 静态色 `--dsw-static-deepseek-450/500` 不随主题变；OFF 态只用语义变量 |

失败前的最后一道自检：把 `swarmMode` 改 `true` 后**看你自己下一轮的系统提示**——
没出现那段，就是装配/热提交断了，不是模型不听话。

---

## 四、与设计稿的四偏差（实测修正，后续人别再按旧稿走）

0. **`conversation.input.permission` 是单占位槽，不能拿来挂第二个控件。** 契约写的是
   `kind: 'single'; scope: 'session'`（`slots.d.ts:345`），渲染器对 single 槽只取
   `entriesOfSlot(key)[0]` —— 注册进去会**把官方权限控件整颗顶掉**。
   首版就是这么挂的，人工验收当场发现「完全权限按钮消失」（其余显示正常，所以纯看截图看不出来）。
   **落点改为 `conversation.input.left`**（`kind: 'list'`，紧随 `div.modes` 之后渲染，视觉上仍在权限右边）。
   探针也已补一条反向断言：**不得注册到任何 `kind: 'single'` 的槽**。
   `order` 取 **-200**：list 槽里第一个位置才紧贴权限控件；已知 `dsh-voice-local` 用 `-100`，
   所以 -200 排在它左边 → `权限 → 蜂群 → 语音`。换环境时若无人占该槽，开关仍在最前；有人用更小 order 只会右移一格，
   **不会挤掉别人**（这正是 list 与 single 的区别，也是公布到 GitHub 时可以放心的地方）。
1. **profile patch 里的覆盖条目会被 settings 写入按需删除，这是设计不是丢配置。**
   `ConfigEditor.edit()` 在「下一份 config 等于继承值」时删掉 `config` 键，条目只剩 `id + name` 就整条删
   （`dsh-config-editor/lib/index.js:99-104`）。所以 `swarmMode: false` 与 bundle 默认相同时，patch 里
   看不到 `dsh-ai-team` 是**正确状态**；点 ON 才会出现 `swarmMode: true`。
   ⇒ 别把说明注释写在这个条目旁边（会跟着消失），也别手工把 `swarmMode: false` 补回去（下次写入又删）。

1. **`--dsw-alias-brand-primary` 不是蓝色。** 主题包里它在浅色=`neutral-bluish-1000`、暗色=`neutral-bluish-50`，
   也就是「近黑 / 近白」的中性对比色。要品牌蓝只能用静态色 `--dsw-static-deepseek-450 (#5686fe)`，
   悬停 `-500 (#4176e6)`，文字固定白。**这是唯一硬编码字色的地方**（因为底色是固定蓝，不是主题色）。
2. **`order` 方向已实测：升序。** `@deepseek-ai/dsh-client-ui-renderer` 里 `sort((a,b) => a.order - b.order)`，
   缺省 `0`；官方权限控件不传 order。所以正数就在右边（设计稿 §七 的未实测项已消）。
3. **「中途切换下一轮生效」→ 实际是「下一个模型步生效」。** system prompt 每个模型步重新装配
   （不是每轮），所以一次工具调用之后就可能已经变色。热切换不需要重开会话、不需要重启进程。

另修一条实现坑（写进 `src/index.js` 注释了）：`apply` 里的 `resolved = { ...DEFAULTS, ...config }`
会把 `volatile` 引用**展开成当时的值**，之后热改就看不见了 —— 开关必须读原始 `config.swarmMode.get()`。

---

## 五、已知风险与限制

| 项 | 说明 | 处置 |
|---|---|---|
| **`dsh plugin add` 会降级依赖** | 2026-11-23 实测把 `@openai/codex` 0.167.2 降回 0.153.4 | 本次用 junction 手工挂载，**没跑 pnpm**；`EVIDENCE-web-install.txt` 记录装机前后都是 0.167.2 |
| **junction 不在 lockfile 里** | 谁下次在 `profiles/web` 跑 `pnpm install`，这个 junction 会被清掉，插件随即消失（工具与开关一起没） | 要长期用就改成正式依赖；改之前先看 §五 第一行 |
| **`configForms.get('dsh-ai-team')` 的入口 id 假设** | 客户端拿开关状态走 Host 的 settings 文档，entryId = 插件行 id。官方实现（`dsh-settings`）用「profile entry id」做 section 名，本仓库的行 id 是 `dsh-ai-team`，两者一致 | 若开关**不出现**，第一嫌疑是这里：先看 `dsh --profile web --dump-config` 的 id 与 `src/client.js` 的 `ENTRY_ID` 是否逐字相同 |
| **纯 prompt 可能被忽略** | 这正是例外通道存在的原因 | 事后核：回复里有没有 `[蜂群例外: …]`，再对实际行为 |
| **全局开关 = 所有会话一起变** | 已确认接受 | 会话级另开单元 |
| **UI 未验** | 需要浏览器刷新页面（client 模块在页面加载时取） | 见 §六 |

---

## 六、待人工验收（一次做完）

1. **刷新浏览器页面**（不是重启 `dsh web`；服务端已就绪）。
2. 看输入框权限控件右边有没有 `蜂群 OFF`。点一下 → 变 `蜂群 ON`（蓝底白字），
   再点回 `OFF`；随后看 `profiles/web/cordis.patch.yml` 里 `swarmMode` 是否跟着变（写回成功与否的硬证据）。
3. 切到**暗色主题**再看一眼：OFF 态应是深底浅字描边，不得出现白斑。
4. 开启后发一句实现类小事（如「在 README 加一行」），看它**是否先调 `ai_team_plan`**。
5. 关闭后同一件事应恢复直接动手。

---

## 七、回滚

```powershell
# 1) 关掉开关（不删插件也能立刻恢复原生行为）
#    编辑 <DSH_HOME>/profiles/web/cordis.patch.yml 的 dsh-ai-team 条目：swarmMode: false
# 2) 要彻底摘掉插件：
Remove-Item "$env:USERPROFILE\.dsh\profiles\web\node_modules\dsh-ai-team" -Force      # junction，不删目标
#    然后从 profiles/web/package.json 的 bundles 与 cordis.patch.yml 里删掉 dsh-ai-team 条目
#    备份：package.json.bak-20261124-swarm · cordis.patch.yml.bak-20261124-swarm
```

> 说明：最初想用 `dsh --profile headless --patch …` 实跑一次真模型来验注入，但 headless 没有 DeepSeek 凭据
> （`MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"`），临时 overlay 已删除。
> 改用零模型调用的装配探针（`probe-prompt.mjs`），证据强度反而更高（直接看渲染结果，不受模型行为影响）。
> 该探针依赖本机 `node_modules`（指向 DSH 本体），在干净 clone 里跑不了 —— 它是本机证据工具，不是发布件。
