# 蜂群模式（Swarm Mode）· 设计方案

> 状态：**已实施（2026-11-24）**。方案 2026-11-24 与需求方讨论定稿，同日落地；
> 服务端注入链路已机器验证通过，**UI 控件待人工验收**（见 `DELIVERY.md` §三 / §六）。
> 实施中修了三处设计假设（品牌蓝变量不是蓝、`order` 已实测升序、热切换是「下一个模型步」而非「下一轮」）
> —— 见 `DELIVERY.md` §四，**别再按本文旧值重做**。

---

## 一、目标（一句话）

在 DSH Web 输入框的权限控件右边加一个开关，开了之后 **Lead 从「工人」降为「调度员」**——
实现类任务必须走 `ai_team_plan → ai_team_gate → ai_team_run` 编排，不自己动手；关了回到原生状态。

---

## 二、已定决策（需求方拍板，**别再讨论**）

| # | 决策 | 理由 |
|---|---|---|
| 1 | **用提示词注入，不用 `tools.restrict` 硬拦截** | 工具表是**会话启动时装配**的，中途切换不生效；prompt 是**每轮重新组装**，中途开立刻生效。需求方原话："工作某两轮中间开了，也能立马提醒 Lead" |
| 2 | UI 放 `conversation.input.permission` 插槽，排在权限选择器**右边** | 与权限档语义相近（都在答"这个会话怎么工作"），同插槽天然并排 |
| 3 | 文案两态都带状态词：**`蜂群 ON`** / **`蜂群 OFF`** | 只写「蜂群」新用户会误以为已开启 |
| 4 | 开启态：品牌蓝底 + 白字；关闭态：语义变量（浅色主题下观感即白底黑字） | 见 §三·3 —— **不能硬编码 `#fff` 底色**，会被暗色主题打脸 |
| 5 | 状态先做**全局开关**（settings），不做会话级 | 「中途开启」的需求全局已满足；会话级要摸 session 状态存储，成本高、非当前需求 |
| 6 | `decide` 节点**本轮不做** | 那是改引擎（`manager.schema.json` + Gate），要单独开单元走完整验收 |

---

## 三、已验证的技术事实（2026-11-24 实查，**不用重查**）

### 1. 插槽
官方权限选择器的注册点（`@deepseek-ai/dsh-client-ui-permission-presets/lib/client.js:803`）：

```js
ctx.slots.inject("conversation.input.permission", () => ctx.slots.register({
  name: "conversation.input.permission",
  locale: PERMISSION_ACCESS_NS,
  inject: (sessionId) => ({ ... }),      // ← 拿得到 sessionId（本方案先不用）
}, PermissionSelect));
```

- 插槽名：**`conversation.input.permission`**
- `register()` 支持 **`order`** 字段（同一文件 `:796` 的 `settings.general.item` 用了 `order: -20`）
- ⚠️ **这个槽是 `kind: 'single'`，单占位** —— 见下面实测。

> **实施实测（2026-11-24，这条推翻了本文原判断）**：
> `conversation.input.permission` 在契约里是 `kind: 'single'; scope: 'session'`
> （`dsh-client-ui-conversation/lib/types/client/contract/slots.d.ts:345`），渲染器对 single 槽
> 只取 `entriesOfSlot(key)[0]`。**注册进去会顶掉官方权限控件**（实测：整颗「完全权限」选择器消失，只剩自己的按钮）。
> 「同槽可多人」只对 **`kind: 'list'`** 成立。
> 正确落点是 **`conversation.input.left`**（`kind: 'list'`、`scope: 'session'`、无 owner）：
> composer 工具行的渲染顺序是 `div.modes`（permission + plan）→ `renderSlot("conversation.input.left")`，
> 所以它**视觉上就在权限控件右边**，且允许多 occupant 共存（`dsh-cordis-client-runner` 的文档示例也拿它举例）。
> 排序方向已证实为**升序**——`@deepseek-ai/dsh-client-ui-renderer/lib/client.js` 里
> `[...rows].sort((a, b) => a.order - b.order)`，缺省 `order ?? 0`。
> **落点的 order 取负值 `-200`**：`list` 槽的**第一个位置**才紧贴 `permission + plan`。
> 已知占位是 `dsh-voice-local` 的麦克风按钮（`order: -100`），所以 -200 落在它左边 →
> 输入框左侧顺序为 `权限 → 蜂群 → 语音`（2026-11-24 按需求方要求调整；先前的 +100 会排到语音右边）。
> 跨环境兼容：没人占该槽时本开关仍在最前；有人用更小的 order 时它只会右移一格，**不会挤掉任何控件**（与 single 槽相反）。

### 2. 官方颜色变量（`@deepseek-ai/dsh-client-ui-theme`）

```css
--dsw-static-deepseek-400: #7aaaff
--dsw-static-deepseek-450: #5686fe     /* 最接近品牌蓝 */
--dsw-static-deepseek-500: #4176e6
--dsw-alias-brand-primary              /* 语义层，随主题自动适配 —— 优先用这个 */
--dsw-alias-label-primary / -secondary / -tertiary
--dsw-alias-border-l2 / -l4
--dsw-alias-bg-module-platform
--dsw-alias-interactive-bg-hover
```

### 3. 有暗色主题（**这条最要紧**）
主题包里有 `body[data-ds-dark-theme]` 整套覆盖。所以：
- **禁止**硬编码 `background: #fff` —— 暗色主题下会变成一块刺眼白斑
- 正确做法：关闭态用 `transparent` + `--dsw-alias-border-l4` 描边 + `--dsw-alias-label-secondary` 文字
  → 浅色主题下**看起来就是白底黑字**，暗色下自动变深底浅字。**两套主题都成立**

> **实施实测（2026-11-24）**：`--dsw-alias-brand-primary` **不是蓝色**——
> 它在浅色解析成 `--dsw-static-neutral-bluish-1000`（近黑）、暗色成 `-50`（近白），是中性对比色。
> 想要「品牌蓝」只能用静态色：底色 `--dsw-static-deepseek-450 (#5686fe)`、悬停 `-500 (#4176e6)`，
> 文字固定 `#fff`（底色是固定蓝，不随主题变，所以这里不是「硬编码底色」那个坑）。

### 4. 客户端插件范式参考
- `~/.dsh/profiles/web/node_modules/dsh-workspace-write-plus/src/client.js`
  （ModuleLoader bundle、`settings.section` 注册、`settingsScope.bind`、locale 字典、样式注入）
- `~/.dsh/profiles/web/node_modules/dsh-backlog/lib/client.js`（更小的样板）

### 5. 服务端插件范式
`dsh-gsv-tts` / 官方 `tool-agent-team` 的 `export { Config, apply, inject, name }` 形状；
system prompt 注入参考 `node_modules/@deepseek-ai/dsh-experimental-tool-agent-team/lib/index.js` 的
`scoped.systemPrompt.getContextOrder(...)` + `register({ name, order, required, text })`。

---

## 四、注入文案草案（实施时可直接用，可再润色）

```markdown
## 蜂群模式 · 已开启

你现在是**调度员**，不是工人。

1. 实现类任务（写/改文件、建文档、跑会改变状态的命令）**必须先 `ai_team_plan` 出图**
   → 图过 `ai_team_gate` 判据 → 由 `ai_team_run` 执行。你不亲自实现。
2. 只读操作（读文件、搜索、只读命令、回答问题）不受此限。
3. 需要拍板的边界，用 `ai_team_plan` 的节点表达，**不要自己决定再告诉我**。
4. **例外通道**：如果你判断本规则不适用（例如只是问答、或规模小到出图反而更慢），
   在回复开头写 `[蜂群例外: 理由]` 然后继续。**不写声明就绕过 = 违规。**
5. 关闭方式：输入框右侧「蜂群 ON」点一下切成 OFF。
```

第 4 条是设计枢纽：把「违反」变成「**必须显式声明的偏离**」，事后核对不用猜
（看回复里有没有 `[蜂群例外: …]`，再对实际干了什么）。与验收官的「无证据不算通过」同一思路。

---

## 五、实施步骤（新对话照做）

1. **`src/index.js`**
   - `Config` 加 `swarmMode: Schema.boolean().default(false).volatile().description(...)`
     —— **`.volatile()` 不是可选项**：只有 volatile 字段才能被 settings 表单暴露给客户端写，
     且 loader 对它「只提交新值、不重挂插件」（`_commitVolatile`）。
   - `DEFAULTS` 同步
   - `apply()` 里**按需**注入（`ctx.inject(['systemPrompt'], …)`）：`systemPrompt.section({ name, order, text: () => … })`，
     `text` 每轮重组装时调用，所以开关热改立刻反映
   - ⚠️ 开关值必须读**原始** `config.swarmMode.get()`：`{ ...DEFAULTS, ...config }` 会把 volatile 引用展开成当时的值
2. **`src/client.js`**（新建，照 `dsh-workspace-write-plus/src/client.js` 的 ModuleLoader 手写范式）
   - `ctx.slots.inject("conversation.input.permission", …)` 注册开关组件
   - `order` 取正值（排在权限右边）
   - 两态：`蜂群 ON`（品牌蓝底白字）/ `蜂群 OFF`（描边 + 次要文字色）
   - 点击 → 写回 settings → 下一轮生效
   - **状态通道用 `ctx.configForms.get(entryId)`**（官方 `dsh-client-ui-settings` 提供的 Host settings 读写面：
     `getSnapshot()` / `subscribe()` / `set(field, value)`）。⚠️ **没有 `settingsScope` 这个服务**——
     `dsh-workspace-write-plus/src/client.js` 里的 `ctx.get("settingsScope")` 是自研写法，本机 DSH 本体里不存在，
     照抄它会让开关永远停在「读取中」
3. **`package.json`** 的 `dsh.client.inject` 补 `@deepseek-ai/dsh-client-ui-slots`（或按实际插槽依赖）
4. **`cordis.patch.yml`** 的 config 补 `swarmMode: false`
5. 装进 profile → **实测**
   - 实测（2026-11-24）：**不需要重启 `dsh web`、也不需要新开会话**——patch 内容改动会触发 profile 热重载，
     新插件随即便进入工具表；本插件的 section 每模型步重读，开关一改下一步就变。
     （只有 **client 半边**要刷新浏览器页面才取到，那是页面加载时取模块。）
   - 装法别用 `dsh plugin add`：2026-11-23 实测会重解析依赖、把 `@openai/codex` 降级。见 `DELIVERY.md` §五。

---

## 六、验收标准（缺一不算完成）

1. 开关出现在输入框权限控件**右边**，两态文案为 `蜂群 ON` / `蜂群 OFF`
2. 浅色主题下 OFF 是白底黑字观感；**切到暗色主题不出现白斑**
3. 开启后**新开一轮**（不用重开会话）：让它做一件小事（如"给 README 加一行"），
   **观察它是否调 `ai_team_plan`**——这是唯一能证明注入生效的办法，不接受"prompt 写了所以应该生效"
4. 关闭后同一件事恢复为直接动手
5. 开启态下它若不走编排，回复里必须出现 `[蜂群例外: …]`
6. 颜色不遮暗色主题；测试至少覆盖 `light` / `dark` 两套

---

## 七、风险与已知边界

| 风险 | 说明 | 处置 |
|---|---|---|
| **纯 prompt 可能被忽略** | 这就是第 4 条例外通道存在的原因 | 事后核：看有没有 `[蜂群例外]` + 对比实际行为 |
| ~~中途切换不影响正在跑的那一轮~~ | **已实测修正**：system prompt 是**每个模型步**重组装，不是每轮；改开关后下一步就生效 | 文档写明「下一个模型步生效」 |
| 图跑不通时 Lead 救不了？ | 本方案**不夺工具权**，所以不存在这个风险（硬拦截版才有） | — |
| 全局开关 = 所有会话一起变 | 需求方已确认接受 | 要会话级另开单元 |
| ~~`order` 具体数值未实测~~ | **已实测**：升序、缺省 0，取正数即落在权限控件右边 | 见 §三·1 的实测注 |

---

## 八、关联但本轮不做

- **`decide` 节点类型**：`manager.schema.json` 加 `kind: "decide"`（无 writes / 无 accept，带 `ask` + `options[{id,label,cost,recommended}]` + `blocking`），
  同时 `dag.workflow.mjs` 的 Gate 要放行这种节点。**这是改判据，要连带重跑 harness（45 场景 ≈ 100 分钟）**。
- **`accept.type=review` 作为 work 节点主判据**：现在只准 `check` 节点用，导致"写文档"这类活建不出合法节点。
- **Lead 派活清单**：这轮复盘发现"七成该派没派"，需要一个 `templates/LEAD-PLAYBOOK.md`。
- **GitHub 更新**：需求方 2026-11-24 明确「先本地改好再一起推」，**本单元完成前不要 push**。
