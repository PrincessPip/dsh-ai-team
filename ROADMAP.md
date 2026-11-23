# Roadmap · 路线图

> 中文 | **[English](#english)**
> 最后更新：2026-11-24（对应 v0.2.0）
> 说明：本文件只写**尚未实现**的东西。已实现的能力看 [`README.md`](README.md) 与 [`docs/CONTRACT.md`](docs/CONTRACT.md)。

---

## 一、即将实现（下一版候选，按优先级）

| # | 事项 | 为什么要做 | 关键难点 / 代价 |
|---|---|---|---|
| 1 | **`decide` 节点类型** | 现在「需要人拍板的边界」只能塞进 `check` 节点的说明里；引擎没有一等公民的「决策节点」（带 `ask` / `options[{id,label,cost,recommended}]` / `blocking`） | 要同时改 `engine/manager.schema.json` 与 `engine/dag.workflow.mjs` 的 Gate 放行规则 —— **属判据类改动，必须连带重跑 40 场景回归台**（全量约 100 分钟） |
| 2 | **`accept.type=review` 作为 work 节点主判据** | 现在只有 `check` 节点能拿 `review` 当主判据，导致「写文档」这类**没有可执行断言**的活建不出合法节点，只能硬凑 | 判据语义要扩：`review` 的 `pass` 依赖验收官裁决，而裁决本身是 E1 级证据；要写清「什么情况下它足以让节点算 ok」 |
| 3 | **发布 / 人工审批节点类型** | 2026-11-24 的真实发布（`git commit` + `push` + 改 GitHub 可见内容）暴露：**DAG 表达不了「需要凭据 + 一次性人工判断」的动作**，只能整段走例外通道 | 与 #1 同源（本质是「非确定性节点」）；还要解决凭据不能进图的问题 —— 图里只能写「做什么」，凭据解析必须留在执行器 |
| 4 | **`templates/LEAD-PLAYBOOK.md`（派活清单）** | 复盘发现「**七成该派出去的活没派**」：Lead 倾向于自己干，而不是先判断「这块该不该交给工人/验收官」 | 纯文档，但要把「什么形状的活必须派」写成可核的判据，否则又变成口号 |
| 5 | **`docs/` 全量英文版** | 目前英文只有 `README.en.md` 与本文件；`docs/` 下 8 份规范（约 220 KB）仍是中文 | 体量大且是判据文本，翻译必须逐条对齐语义；建议按 `CONTRACT.md` → `REVIEW-CONTRACT.md` → `TRIGGER-POLICY.md` 顺序做 |
| 6 | **发布到 npm** | 现在只能从 GitHub 目录装（README §2.2 方式 B）。上 npm 后 `dsh plugin add dsh-ai-team` 才真正可用 | peer 依赖的 prerelease 范围坑（`^0.2.0-rc.2` 不匹配 `0.2.1-rc.2`，只能逐个 tuple 枚举）；发布前要确认 `files` 带上 `tasks/`（夹具） |
| 7 | **回归场景扩容（现 40 个）** | 现有场景覆盖谎报 / 少报 / 越界 / 篡改证据 / 重试 / 并行 / 条件跳转；**缺**「并行写同一文件」「跨平台路径」「超长输出截断」三类 | 加场景要连带更新 `fixtures/graphs/` 与文档清单；且 fixture 不幂等，场景必须自带 sandbox 还原 |

## 二、计划中（方向确定，未排期）

- **跨平台引擎**：`engine/` 目前绑定 Windows + PowerShell（`*.ps1` 与 Job Object 杀树）。Linux / macOS 上要跑，需要一份替代实现 + 逐条对齐行为。
- **`verify-product` 随附**：判「产物对不对」的那把尺子没跟过来（README §6·3），目前只能各使用者自己写。
- **会话级蜂群开关**：现在是**全局**开关（一开所有会话一起变）。会话级要摸 session 状态存储，成本明显更高。
- **116 份真实裁决 → 回归集**：`docs/decisions/` 里已有 116 份脱敏裁决，可以做成「改裁决解析器后跑一遍，看归一化结论有没有漂」的台子。
- **验收官顺从率的量化**：知道「契约 + 证据强制抬高了糊弄成本」，但**没有数字**。需要一套红队样本集才能谈百分比。

## 三、明确不做（附理由，别再来问）

| 不做 | 理由 |
|---|---|
| 绑定某个模型厂商 | 后端本来就是可插拔的（codex / 任意 OpenAI 兼容）。绑定等于把判据卖给一家 vendor。 |
| 重造 teammate / 任务板 / 直投消息 | 官方 `dsh-experimental-agent-team` 已有，且做得比我们好。我们的价值在**判据**，不在协同基础设施。 |
| 进程级 confinement（真只读沙箱） | 实测代价过高：Windows 上 `--sandbox read-only` 会禁掉一切命令，验收官连文件都读不到。本项目选择「给能力 + 事后可核 + 如实标注执法等级」，**并且绝不把「事后可核」说成「隔离」**。 |
| 自动补签 / 自动通过 | 与 §1 的核心承诺直接冲突。裁决缺失、自相矛盾、证据为空一律降级，**没有「默认通过」这条路**。 |

## 四、需要证据才能推进（不猜）

- **`headless` 引擎在多模型下的稳定性**：只在少数模型上真跑过，换模型后的重试/升档行为没系统采样。
- **Windows 长路径 / 链接**：`runner.py` 的快照对 junction 的处理是已知缺口（`snapshot()` 静默跳过链接），要改得连带重跑回归台。
- **压缩后的证据可读性**：`raw` 截断到 20000 字符，长验收报告被截断后是否还足以支撑裁决，没有量化。

---

## English

> **English** | [中文](#roadmap--路线图)
> Last updated: 2026-11-24 (for v0.2.0)
> This file lists **what is not implemented yet**. For what works, see [`README.md`](README.md) / [`README.en.md`](README.en.md) and [`docs/CONTRACT.md`](docs/CONTRACT.md).

### 1. Coming next (candidates for the next release, in priority order)

| # | Item | Why | Cost / hard part |
|---|---|---|---|
| 1 | **`decide` node type** | "A boundary only a human can decide" currently has to be smuggled into a `check` node's notes; the engine has no first-class decision node (with `ask` / `options[{id,label,cost,recommended}]` / `blocking`) | Touches `engine/manager.schema.json` *and* the gate rules in `engine/dag.workflow.mjs` — **a criteria change, so the 40-scenario regression bench must be re-run** (~100 minutes for the full set) |
| 2 | **`accept.type=review` as a work node's primary criterion** | Only `check` nodes may use `review` today, so work with no executable assertion (writing docs) cannot be expressed as a legal node | Needs a clear rule for when a reviewer verdict (itself E1-grade evidence) is enough to make a node `ok` |
| 3 | **Release / human-approval node type** | A real release on 2026-11-24 (`git commit` + `push` + changing publicly visible content) exposed that **a DAG cannot express actions requiring credentials plus one-off human judgement** — the whole thing had to use the escape hatch | Same root as #1 ("non-deterministic node"); plus credentials must never enter the graph — the graph says *what*, credential resolution stays in the runner |
| 4 | **`templates/LEAD-PLAYBOOK.md` (delegation checklist)** | A retrospective found that **seven out of ten tasks that should have been delegated were done by the Lead itself** | Pure documentation, but "which shapes of work must be delegated" needs to be checkable, or it is just a slogan |
| 5 | **Full English `docs/`** | English currently covers only `README.en.md` and this file; the eight specs under `docs/` (~220 KB) are still Chinese | Large and criterion-bearing text; suggested order: `CONTRACT.md` → `REVIEW-CONTRACT.md` → `TRIGGER-POLICY.md` |
| 6 | **Publish to npm** | Install currently requires the GitHub directory (README §2.2 route B); on npm, `dsh plugin add dsh-ai-team` finally works as advertised | The prerelease peer-range trap (`^0.2.0-rc.2` does not match `0.2.1-rc.2`; enumerate tuples explicitly); also make sure `files` includes `tasks/` (the fixtures) |
| 7 | **More regression scenarios (40 today)** | Existing scenarios cover overclaiming / under-reporting / out-of-scope writes / evidence tampering / retries / parallelism / conditional jumps; **missing** parallel writes to one file, cross-platform paths, and very long output truncation | New scenarios must update `fixtures/graphs/` and the docs; the fixture is not idempotent, so each scenario needs its own sandbox restore |

### 2. Planned (direction agreed, not scheduled)

- **Cross-platform engine**: `engine/` is bound to Windows + PowerShell (`*.ps1` and Job Object process-tree kill). Linux / macOS needs an alternative implementation with behaviour aligned item by item.
- **Bundle `verify-product`**: the ruler for judging *products* did not come along (README §6·3); every user writes their own today.
- **Per-session swarm switch**: the current switch is **global** (one toggle changes every session). Per-session needs the session state store — clearly more expensive.
- **116 real verdicts → regression set**: `docs/decisions/` already holds 116 redacted verdicts; they could become a bench that catches drift whenever the verdict parser changes.
- **Quantify reviewer sycophancy**: we know the contract plus mandatory evidence raises the cost of faking, but **we have no number**. That needs a red-team sample set before any percentage is meaningful.

### 3. Explicitly not planned (with reasons)

| Not doing | Why |
|---|---|
| Binding to a model vendor | Backends are pluggable by design (codex / any OpenAI-compatible API). Binding would sell the criteria to one vendor. |
| Re-implementing teammates / task board / direct messages | The official `dsh-experimental-agent-team` already does this, and better. Our value is **criteria**, not coordination plumbing. |
| Process-level confinement (a truly read-only sandbox) | Measured cost is too high: on Windows `--sandbox read-only` forbids every command, so the reviewer cannot even read files. This project chooses "grant capability + check afterwards + label the enforcement level honestly" — and **never sells "checkable afterwards" as "isolated"**. |
| Auto-signing / auto-passing | Directly contradicts the core promise in §1. Missing verdicts, self-contradiction and empty evidence are always downgraded; **there is no "default to pass" path**. |

### 4. Blocked on evidence (not guessing)

- **`headless` engine stability across models**: only really run on a few models; retry/escalation behaviour after switching models is not systematically sampled.
- **Windows long paths / links**: the runner's snapshot has a known gap around junctions (`snapshot()` silently skips links); changing it drags the whole regression bench along.
- **Evidence readability after truncation**: `raw` is truncated at 20000 characters; whether a truncated long review still supports its verdict has not been quantified.
