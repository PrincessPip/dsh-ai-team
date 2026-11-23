# dsh-ai-team

> 给 DeepSeek Harness 配一个**不听话的验收官**：三权分立、三态裁决、无证据不算通过。

---

## 0. 一句话定位

DSH 官方的 agent-team 解决「**谁来干**」；本项目解决「**干得对不对**」。

| | 官方 `dsh-experimental-agent-team` | 本项目 `dsh-ai-team` |
|---|---|---|
| 解决的问题 | 分工与协同：谁来干、干哪块、消息怎么投 | **验收与编排**：干得对不对、下一步怎么排 |
| 提供什么 | teammate 名册、共享任务板、直投消息、`writeScopes` 重叠告警 | 独立验收官、三态裁决、证据强度门槛、DAG 出图与波内并行、闸门链 |
| 成员关系 | 扁平 roster，只协调不 confinement | 三角关系：**工人干活 → 验收官复核 → 只有说不清时才找人** |

**两者是互补关系，可以同时装**：官方管协同，本项目管判据。

本项目**不重造** teammate / 任务板 / 直投消息（官方已有），**不绑定**任何模型厂商（后端可插拔），**不内置**任何私有环境（代理、VPS、订阅）。

---

## 1. 为什么需要它

**因为 LLM 自己说「我干完了」不可信。**

这不是对某个模型的不信任，是结构问题：

- **它读得到自己的漂亮总结，然后盖章。** 只要把「工人的自述」放在「它自己跑的证据」前面，验收官就退化成复述机。
- **「跑了一遍」和「验过了」看起来一样。** 回一句「接口返回 200」是单点观察；而真实故障常藏在第二个观察维度里（进程在、端口在、日志在，但业务是错的）。
- **没有证据的结论永远拿得到。** 只要允许「应该没问题」「理论上成立」这类措辞，返工就变成了措辞游戏。

所以本项目做了三件事，每件都对应上面一条：

1. **三权分立** —— 工人（实现 + 自测 + 写交付包）· 验收官（只读复核，唯一出裁决）· 仲裁（人，只在 `escalate` 时出现）。**谁都不给自己发合格证。**
   ⚠️ 这里的「只读」是**纪律 + 事后可核，不是沙箱隔离** —— 见 §3 末尾那段。
2. **三态裁决** —— 只许 `pass` / `rework` / `escalate`，没有「基本可以」。第二态带最小修改指令打回，第三态打包成一个「需要人做的决定」。
3. **无证据不算通过** —— 每条 `pass` 必须带 `file:line` 或命令输出；证据分 `E0`–`E3` 强度，引不出证据的 `pass` 强制降级为 `rework`。这套规则由闸门和解析器机械执行，**不靠验收官的自觉**。

> 一句话：把「我干完活自己说好了」换成「有人独立核过，而且核的过程留下了能被别人重跑的证据」。

---

## 2. 5 分钟上手

### 2.1 前置

| 需要 | 说明 |
|---|---|
| DSH | 已装并能跑 `dsh --version`（本仓库按 `0.2.x` 的插件范式编写） |
| Node.js | ≥ 18（插件本体是 ESM） |
| Python 3 | ≥ 3.9（`ai_team_plan` / `ai_team_gate` / `ai_team_run` 调 `engine/` 下的引擎） |
| 验收后端（二选一） | **codex CLI**（`codex exec`，能读写工作区）或**任意 OpenAI 兼容 API**（纯文本，无文件读权） |
| 依赖 `@deepseek-ai/dsh-tools` | peer 要求 `^0.2.0-rc.2`；npm 的 `latest` 标签是 `0.0.1-rc.1`（**不满足**），得显式选版本 —— 见 §2.2 |

### 2.2 安装

#### 先解决依赖（这一步跳不过去）

本插件依赖两个包，**来源不同**：

| 包 | 要求 | 怎么拿 |
|---|---|---|
| `@deepseek-ai/schemastery` | 普通依赖 `^3.18.1` | npm 上有（`latest` = `3.19.0`），正常装即可 |
| `@deepseek-ai/dsh-tools` | **peer** `^0.2.0-rc.2` | ⚠️ 见下 |

`@deepseek-ai/dsh-tools` 的坑不在「有没有」，在 **dist-tag**：
npm 上它的 `latest` 是 **`0.0.1-rc.1`**，**不满足** `^0.2.0-rc.2` ——
所以直接 `npm install @deepseek-ai/dsh-tools` 装到的是那个旧版，插件跑不起来。
（满足范围的版本 npm 上其实有，挂在 `next` 标签下：`0.2.2-rc.5`。）

三条路，任选一条：

```powershell
# ① 明确要一个满足范围的版本（走官方入口，profile 由 pnpm 维护）
dsh plugin --profile web add "@deepseek-ai/dsh-tools@next"     # 或钉一个具体的 0.2.x

# ② 直接用本机 DSH 自带的那份（0.2.1-rc.2），链接进 profile
New-Item -ItemType Junction `
  -Path "$env:USERPROFILE\.dsh\profiles\web\node_modules\@deepseek-ai\dsh-tools" `
  -Target "<npm 全局前缀>\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-tools"

# ③ 把本插件装进 DSH 自己的安装树（Node 沿目录向上查找，命中隔壁的 dsh-tools）
cd "<npm 全局前缀>\node_modules\@deepseek-ai\dsh"
npm install /path/to/dsh-ai-team
```

> **怎么确认成功了**（这两条才是判据，比抄命令可靠）：
>
> ```powershell
> cd "$env:USERPROFILE\.dsh\profiles\web"      # 换成你的 profile
> node -e "console.log(require.resolve('@deepseek-ai/dsh-tools'))"
> node -e "console.log(require('@deepseek-ai/dsh-tools/package.json').version)"   # 要满足 ^0.2.0-rc.2
> ```
>
> ⚠️ **本节写清的是「机制 + 判据」，不是逐字可复制的安装脚本。**
> 版本号（`latest` = `0.0.1-rc.1` / `next` = `0.2.2-rc.5` / DSH 自带 `0.2.1-rc.2`）与两个路径是实测核过的；
> 具体用哪条路，取决于你的 profile 由哪个包管理器维护。**以上面两条命令的实际输出为准。**
> 装不上时把这两条的输出贴出来，比描述现象有用。

#### 再装插件

```powershell
# 方式 A：从 registry 装（前提是该包已发布到 npm 或你的私有 registry，包名 dsh-ai-team）
dsh plugin --profile web add dsh-ai-team

# 方式 B：从本仓库目录装（未发布、或要改源码时用这条）
dsh plugin --profile web add /path/to/dsh-ai-team
```

> `--profile` 是必填参数，把 `web` 换成你要挂的那个 profile。
> `dsh plugin add` 会顺带解析依赖 —— 如果它去 registry 找 `dsh-tools` 并报版本不满足，
> 就先用上面任一条路线把依赖就位，再重跑这条命令。
> 装完**不会立刻生效**：工具表在会话启动时装配，**新开一个会话**才看得到 `ai_team_*` 工具。

### 2.3 最小配置

插件配置写在 profile patch 里。把仓库根的 `cordis.patch.yml` 的 `insert` 段并进
`<DSH_HOME>/profiles/<profile>/cordis.patch.yml`（`id` 不要与已有条目重复）：

```yaml
- insert:
    - id: dsh-ai-team
      name: dsh-ai-team
      config:
        backend: codex                          # 验收官后端：codex | openai
        model: ""                               # 留空 = 用后端默认模型
        codexPath: codex                        # codex CLI 可执行文件
        openaiBaseUrl: https://api.openai.com/v1
        openaiApiKeyEnv: OPENAI_API_KEY         # ← 只写「环境变量名」，不写 key 本体
        timeoutMs: 3600000
        engineDir: ""                           # 留空 = 用包内 engine/
        pythonPath: python
        nodePath: node
        outDirName: .ai-team
```

**安全红线**：`openaiApiKeyEnv` 只接受**环境变量名**，永不接受 key 字面量；日志与返回值里不会出现 key。
key 请放环境变量或你的凭据管理器，**不要写进配置文件**。

其它参数的含义与默认值见 [`docs/CONTRACT.md`](docs/CONTRACT.md) §4。

### 2.4 第一次调用

新会话里让模型调这三步（也可以自己按顺序说给它听）：

```text
1) ai_team_init()                                    # 把团队模板铺进当前工作区
2) ai_team_review({ brief: "REVIEW-BRIEF.md", round: "1" })
3) ai_team_gate({ graph: ".ai-team/runs/<runId>/GRAPH.json" })   # 有 DAG 需求时才走这条
```

`ai_team_init` 会铺出这些东西（已存在的跳过，不覆盖）：

```text
<target>/AI-TEAM.md          ← 团队约定片段（贴进或参照你的 AGENTS.md）
<target>/REVIEW-BRIEF.md     ← 任务书模板
<target>/INC-TEMPLATE.md     ← 事故单模板
<target>/.ai-team/roles/     ← 三个角色的提示词：worker / reviewer / fire-chief
```

`ai_team_review` 的返回是一份**结构化裁决**，形状固定（不合规时由 `verdict.js` 降级，绝不默认 `pass`）：

```json
{
  "verdict": "rework",
  "confidence": 0.74,
  "blocking": ["B1: 验收标准 C3 声称「已覆盖」，但引不出 file:line —— 证据缺失"],
  "findings": [
    { "id": "C1", "claim": "新增函数有单元测试", "evidence": "src/x.test.mjs:12「it('...')」", "type": "E2" }
  ],
  "outDir": "/path/to/workspace/.ai-team/reviews/round1",
  "raw": "……验收官原文（截断到 20000 字符）……"
}
```

最小可跑样例（含期望裁决形状）在 [`examples/minimal/`](examples/minimal/)。

---

## 3. 五个工具速查

| 工具 | 一句话 | 参数（`*` = 必填） | 返回 |
|---|---|---|---|
| **`ai_team_init`** | 把团队模板铺进当前工作区 | `target`（默认 cwd）· `force`（默认 false） | `{ created, skipped, root }` |
| **`ai_team_review`** | 送独立验收官，产出三态裁决（核心） | `brief*`（内容或 `.md` 路径）· `round`（默认 `"1"`）· `backend` · `model` · `dryRun` | `{ verdict, confidence, blocking, findings, outDir, raw }` |
| **`ai_team_plan`** | 需求 → DAG 图（调 `engine/manager.py`） | `brief*` · `outDir` · `model` · `dryRun` | `{ graphPath, runId, nodeCount, summary }` |
| **`ai_team_gate`** | 判图：坏图不许进执行器（调 `engine/dag-gate.mjs`） | `graph*`（`GRAPH.json` 路径） | `{ ok, errs, warns, codes }` |
| **`ai_team_run`** | 执行图：按波跑（调 `engine/runner.py`） | `graph*` · `engine`（`headless`/`fixture`/`codex`，默认 `fixture`）· `runId` · `fixtureDir`（仅 `fixture` 生效：场景目录，含 `spec.json`） | `{ runId, result, nodes, runDir }` |

三条使用纪律（也是工具 description 里写明的）：

1. **`ai_team_gate` 返回 `ok=false` 时必须停。** 不许「先跑跑看」——Gate 拒收的图跑起来只会产出没法归因的证据。
2. **`ai_team_run` 的默认引擎是 `fixture`**：假 worker、秒级、不花钱、不写主树。**它只验流程，不验产物**。要真调模型请显式传 `engine: "headless"`。
3. **`ai_team_review` 返回 `rework` 不是异常。** 它是正常结果，读 `blocking` 与 `findings` 干活就行；只有后端不可用才抛错。

`fixtureDir` 不传时依次回落到 `<cwd>/.ai-team/fixtures`、随附的 `scenarios/honest`；
**非 `fixture` 引擎却传了它 → 明确报错**（不静默吞）。
细节与 40 个随带场景的清单见 [`docs/CONTRACT.md`](docs/CONTRACT.md) §3.5。

### 验收官的「只读」到底是什么（别理解错）

**它是纪律 + 事后可核，不是沙箱隔离。** 这三句话各自都成立，缺一句就会误判：

| 说法 | 对错 |
|---|---|
| 「验收官不写工作区」 | ✅ 对 —— 这是它的**行为纪律**，写进提示词与契约 |
| 「所以它跑不起来写操作」 | ❌ **错** —— 没有进程级 confinement，它技术上能写 |
| 「所以越界发现不了」 | ❌ 也错 —— 前后快照（或 `git status`）能**事后**发现改了什么 |

**为什么不干脆用 `read-only` 沙箱？** 因为实测代价太大：Windows 上
`--sandbox read-only` 实际是「**禁止执行任何命令**」，连 `Get-Content` 都被 `blocked by policy` 挡掉 ——
验收官会读不到文件、跑不了命令，只剩「判任务书叙述层」，那样验收等于没做。

所以本插件的口径是：**给能干活的权限，越界靠事后发现，并如实标注执法等级。**
codex 后端的沙箱档在契约里可配置（`codexSandbox`，默认 `workspace-write`），
返回值里会标注执法等级（如 `enforcement: 'snapshot_only'`）——
**「事后可核」不许被说成「隔离」**，这是两个不同的承诺。详见 [`docs/CONTRACT.md`](docs/CONTRACT.md) §5.1。

---

## 4. 和官方 agent-team 一起用

两套东西装在一起不冲突，分工如下：

```text
┌─ 官方 agent-team：谁来干 ─────────────────────────────┐
│  Lead ─ spawn_teammate ──► worker A / worker B        │
│         team_task_*  ◄──►  共享任务板（谁在做什么）    │
│         send_message  ◄──► 直投消息                   │
└──────────────────────────────────────────────────────┘
                          │  一块活干完了
                          ▼
┌─ 本项目：干得对不对 ─────────────────────────────────┐
│  ai_team_review ──► 独立验收官（只读·可事后核）──► 三态裁决 │
│  ai_team_plan/gate/run ──► DAG 出图 → 判据 → 按波执行 │
└──────────────────────────────────────────────────────┘
```

**推荐接法**：任务板上的一个任务交付后，**由不是它本人**的那一方发起 `ai_team_review`（Lead 发起最省事）。工人自己调 `ai_team_review` 来验自己，等于把三权分立拆了一权 —— 工具不拦你，但它没意义。

---

## 5. 仓库里有什么

| 路径 | 是什么 |
|---|---|
| `src/` | 插件本体：五个工具 + 三个后端 adapter + 裁决解析器 |
| `engine/` | **实战原样**迁移的引擎（Python / Node）：经理层、Gate、执行器、闸门链、救火执行器 |
| `tasks/DAG-2026-11-06/fixtures/` | **回归夹具，已随附**（90 文件 / 约 240 KB）：**40 个场景**（`scenarios/*/spec.json`，覆盖谎报 / 少报 / 越界 / 碰证据 / 重试 / 并行 / 条件跳转…）+ 35 张示例图 + 假 worker。`ai_team_run --engine fixture` 靠它就能零成本跑通全流程，也是**现成的回归台**：同一张图只换 `fixtureDir`，就能看到「诚实的工人 pass / 不诚实的工人 fail」。⚠️ 路径不可挪（引擎按原相对路径找它） |
| `templates/` | 团队模板：约定片段、任务书、事故单、三个角色的提示词 |
| `docs/CONTRACT.md` | **接口契约，唯一事实来源**：五个工具的参数与返回、Config、adapter、裁决规则、诚实边界 |
| `docs/` | [`ARCHITECTURE.md`](docs/ARCHITECTURE.md)（架构）· [`PORTING.md`](docs/PORTING.md)（移植）· 已脱敏的契约与策略原文 · 116 份真实裁决 JSON |
| `examples/minimal/` | 最小可跑示例：一份任务书 + 期望的裁决形状 |

想先看判据而不是代码？直接读这三份：

- [`docs/REVIEW-CONTRACT.md`](docs/REVIEW-CONTRACT.md) —— 验收官作业指导书：三态、证据强度 `E0`–`E3`、观察类别 `D1`–`D5`、反顺从规则
- [`docs/TRIGGER-POLICY.md`](docs/TRIGGER-POLICY.md) —— 什么时候动用验收官（三档：免检 / 留存 / 送审）、救火档、送审前置闸门
- [`docs/decisions/`](docs/decisions/) —— 116 份真实裁决，能直接看到「证据不够」长什么样

---

## 6. 诚实边界

**这一节不美化。** 这个项目本身讲的就是「无证据不算通过」，README 吹牛等于自打脸。

1. **引擎（`engine/`）是「实战原样」，不是「跨平台重写」。**
   绑定 Windows + PowerShell + 特定后端（codex CLI / 本地网关）的部分**一行都没重写**。
   逐条清单（具体到 `文件:行`）与改动点见 [`docs/PORTING.md`](docs/PORTING.md)。
   —— 在 Linux / macOS 上直接跑 `engine/` 下的东西会失败，这不是「没测」，是**设计上没做过**。

2. **`fixture` 引擎是假 worker —— 但它是「能跑的」，不是「缺一块的」。**
   夹具已整目录随附（`tasks/DAG-2026-11-06/fixtures/`，90 文件 / 约 240 KB），
   一条命令就能跑通全流程，**零成本、不调模型、不写主树**：

   ```powershell
   python engine/runner.py tasks/DAG-2026-11-06/fixtures/graphs/g3.json `
     --engine fixture --fixture-dir tasks/DAG-2026-11-06/fixtures/scenarios/honest
   ```

   期望：`gate OK` · 3 波 · 3 节点全 `ok` · `result PASS` · 证据包落 `runs/<id>/`。
   它验证的是**流程**（排队、波次、重试、升档、证据包结构），**不验产物**，也不调任何模型 ——
   `headless` 才真调模型。所以 `fixture` 跑出来的 `result=pass` **不能当交付证据**：
   证据包里那个 `FAKE` 标记文件就是拦住这种误用的。

   ⚠️ **fixture 不幂等：它会被自己的上一次跑搞坏。** 场景会改
   `tasks/DAG-2026-11-06/fixtures/sandbox/` 下的文件（`honest` 就把 `notes.md` 从 3 条写成 4 条）。
   第 2 次跑同一场景时写进去的内容与盘上一模一样 → **零改动** → 被判 `overclaim`，
   **失败字符串与真正的 overclaim 场景完全一样**。所以：**跑前先还原那个 sandbox 目录**，
   否则你看到的是一个假失败。逐条说明与还原命令见 [`docs/PORTING.md`](docs/PORTING.md) §9.1。

   ⚠️ **同一时刻只能有一个 runner 在跑。** `runs/` 进快照，所以并发跑的两个 run 会互相看到
   对方在写证据包，被判成 `evidence_write` 而失败 —— 这不是 bug，是「证据不可被污染」那条判据在生效。
   危害不是「跑不通」，是**结论变假**（你会拿到一个看起来像真失败的 FAIL）。撞上了就串行重跑，
   不要去关那条判据。详见 [`docs/PORTING.md`](docs/PORTING.md) §9.2。

3. **`result=pass` 只代表流程跑通，产物对不对要另跑 `verify-product`。**
   每个真跑单元在原工程里另有一份 `verify-product.py` 判产物 —— **本仓库没有随附它**。
   也就是说：这里能跑出「流程 pass」，但**判断产物是否正确的那把尺子没跟过来**，你得自己写。

4. **验收官仍可能顺从（LLM 通病）。**
   本仓库的对策是**契约约束 + 证据强制**：三态只许三选一、每条 `pass` 必须带证据、引不出就降级、
   证据按强度分档、闸门机械核。这些**抬高糊弄成本**，但**不承诺 100%**。
   它能挡住「随口说好了」，挡不住精心编造的两类不同观察 —— 真正的防线仍是抽检翻旧账与人自己复看。

补充两条同级别的事实（不属契约 §8，但同样是边界）：

5. **本 README 的「5 分钟上手」按 [`docs/CONTRACT.md`](docs/CONTRACT.md) 的契约编写。**
   若你装到的版本与本文不一致，**以契约为准**（并以工具实际的 `description` 为准）。
6. **`engine/` 里的 `*.ps1` 需要 PowerShell，而它影响的入口比你想的多。**
   本仓库不提供这些脚本的跨平台替代品。逐项说清：

   | 入口 | 真的要 PowerShell 吗 |
   |---|---|
   | `ai_team_gate`（纯 Node） | **不需要** |
   | `ai_team_plan` 真跑 | **需要** —— 会先跑出站自检（`manager.py:423-424`） |
   | `ai_team_plan` 的 `dryRun` | 不跑自检，但**仍要求能找到 codex CLI**（`manager.py:411`） |
   | `ai_team_run` + `fixture` / `headless` | **不需要**（`runner.py:2004-2009` 明确跳过自检） |
   | `ai_team_run` + `codex` | **需要** —— 出口自检硬拦（`runner.py:1998-2003`） |
   | 账本 / 对账 / 索引 / 回填（那四个 `.ps1`） | **需要**，且没有替代路径 |

   非 Windows 上怎么处理见 [`docs/PORTING.md`](docs/PORTING.md) §1 与 §4。

---

## 7. 许可

MIT，见 [`LICENSE`](LICENSE)。

`engine/` 与 `docs/` 下的原始材料来自同一位作者的生产工程，已做脱敏处理；
脱敏范围与「哪些文件没有公开」的理由见 [`reports/REDACTION-REPORT.md`](reports/REDACTION-REPORT.md)。
