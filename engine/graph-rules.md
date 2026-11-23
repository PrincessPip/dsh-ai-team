# 图的硬规则（经理必读 · 违反任一条 → Gate 整图拒收）

> 这份是给**经理**（`claude -p --model opus`）看的，不是给人看的作业指导书。
> 判据的真身在 `scripts/dag.workflow.mjs` 的 `validate()`；这里只列「会把你整张图打回来」的条目。
> 每条后面括号里是 Gate 的 code —— 修复轮的错误清单里会原样出现这些 code。
>
> ⚠️ 已知漂移风险：本文件是**人写的摘要**，与 Gate 源码是两份。`scripts/manager.py` 启动时会核
> 「本文件点名的每个 code 是否真在 `dag.workflow.mjs` 里存在」，对不上就中止 —— 但那条只挡得住
> 「摘要编了个不存在的 code」，挡不住「Gate 新加了规则而摘要没写」。后者只能靠修复轮兜。

---

## 一、图的形状

1. 顶层必须有 `v: 1`、`plan_id`（字符串）、`nodes`（非空数组）。（`GRAPH_VERSION` / `GRAPH_NODES`）
2. 每个节点必须有 `id`（全图唯一，小写字母数字下划线）、`kind`、`do`、`accept`（非空数组）、`on_fail`。
   （`NODE_SHAPE` / `NODE_KIND` / `NODE_DO` / `NODE_ACCEPT` / `NODE_ON_FAIL`）
3. `id` 不许用 `__proto__` / `constructor` / `toString` 这类 JS 原型键。（`NODE_ID_RESERVED`）
4. `kind` 只能是：`scout`（只读侦察）、`contract`（定接口）、`work`（实施，只交 diff）、
   `integrate`（唯一写手）、`check`（只读核查）、`replan`（返工规划）。（`NODE_KIND`）

## 二、验收标准（最容易踩）

5. **`work` 节点必须至少有 1 条 `type: cmd` 或 `type: assert` 的 accept。纯文字验收直接拒收。**
   （`NODE_ACCEPT`，DESIGN §4 的 Gate#4）
6. **`integrate` 节点同理** —— 它是唯一写手，落地动作必须能自证。这条比 DESIGN 原文严一档。（`NODE_ACCEPT`）
7. `type: review` **只能出现在 `check` 节点**。（`NODE_ACCEPT`）
8. `cmd` 必须带 `run`；`assert` / `review` 必须带 `rule`。（`NODE_ACCEPT`）

## 三、scope 断言：allowlist 必须算得出来（F1 的血泪）

9. **`type: scope` 的断言，节点必须能给出 allowlist** —— 要么有静态 `writes`，要么有 `allow_from`。
   两样都没有 → 这句断言在解释器里不存在，整图拒收。（`SCOPE_UNCHECKABLE`）
10. `allow_from` 的元素是 `"<上游节点id>.<字段>"`，且那个字段**必须是数组类**：
    `allow_paths` / `changed` / `artifacts` / `missing`。推荐专门用 `allow_paths`（语义就是「允许下游改的路径」）。（`SCOPE_FROM_NOT_ARRAY`）
11. `allow_from` 引用的节点必须存在、**必须是本节点的祖先**、且该字段在该上游的 `out` 里声明过。
    （`SCOPE_FROM_UNKNOWN` / `SCOPE_FROM_NOT_ANCESTOR` / `SCOPE_FROM_UNDECLARED`）
12. **`scope.rule` 的文字里如果点名了「某个上游节点.某个字段」，那句引用必须同时出现在 `allow_from` 里。**
    否则就是「文字一套、机器一套」：模型照文字做会被判越界，照机器做又违背文字。
    （`SCOPE_RULE_UNCHECKED`）
    > 想避免这个坑，最简单的写法是：rule 文字里只描述结论（「changed ⊆ writes 与 allow_from」），
    > 具体清单交给 `allow_from` 字段。
13. `allow_under`（可选）是路径前缀白名单，约束 `allow_from` 动态取到的那部分 —— 落在前缀外的路径会被
    **剔除**，然后按越界判失败。上游给什么，不等于你就能写什么。
13·五、**写集与 `allow_under` 前缀都不许指向「快照排除区」**（2026-11-10 / F6）：`runs/`、`node_modules/`、
    `.git/`、`__pycache__/`、`.pytest_cache/`、`.venv/`。runner 的快照**看不见**这些目录，所以声明写它们等于
    声明一个「既不会判 underclaim 也不会判 overclaim」的写 —— 空话比没有断言更糟，整图拒收。（`SCOPE_EXCLUDED_PATH`）
    > 证据包（`runs/<id>/`）是**验收方的地盘**：员工在里面写东西会被判 `evidence_write`（不可洗）。
    > 需要落盘就落到 `tasks/`、`decisions/`、`scripts/` 这些快照内的地方。
14. ⚠️ **任务卡上会印出机器实际会核的 allowlist**（`describeAccept` 生成的）。那份清单才是判定口径；
    `rule` 文字只是给人看的说明，解释器不读。

## 四、依赖与写集（最容易踩）

15. **不许成环**，也不许 `after` 指到自己。（`CYCLE` / `EDGE_SELF`）
16. `after` 里的 id 必须存在。（`EDGE_UNKNOWN`）
17. **两个非祖先关系的节点，`writes` 不许有交集** —— 想写同一个文件，就得让它们成为前后代，或者
    只让 `integrate` 写。（`WRITE_CONFLICT`）
    > 「非祖先关系」= 谁也不在谁的 `after` 传递闭包里。并行兄弟不能写同一个文件。
18. **`hot_files` 里的文件只有 `integrate` 能写**；别的节点想改，走 `requests` 提请求。（`HOT_FILE_WRITE`）
19. **`kind: check` 不许有 `writes`** —— 只读核查员有写集是自相矛盾。（`CHECK_WRITES`）

## 五、条件与引用（最容易踩）

20. `when.ref` 必须是 `"<上游id>.<字段>"` 形式，`op` 只能是 `eq` / `in` / `exists`。
    （`NODE_WHEN`）`op=in` 时 `val` 必须是数组；`op=eq` 时 `val` 是字符串。
21. **`ctx.from` / `when.ref` / `allow_from` 引用的节点必须是它的祖先**（在 `after` 的传递闭包里），
    否则取值时上游可能还没跑。（`WHEN_NOT_ANCESTOR` / `FROM_NOT_ANCESTOR` / `SCOPE_FROM_NOT_ANCESTOR`）
22. **被引用的字段必须在上游节点的 `out` 里声明过** —— schema 里没声明的字段，模型根本回不出来。
    （`WHEN_UNDECLARED` / `FROM_UNDECLARED` / `SCOPE_FROM_UNDECLARED`）`status` 是例外，永远可用。
23. `out` 里只能出现这些字段名：`summary` / `missing` / `artifacts` / `changed` / `allow_paths` /
    `facts` / `iface` / `patch` / `patch_targets` / `verdict` / `notes`。（`NODE_OUT`）

## 五·五、写节点的两种做法：交 patch 还是落盘（DESIGN 第 5 节决策 3 的血泪）

`tasks/DAG-2026-11-06/DESIGN.md:203`「唯一写手是集成节点」那一条定的 v1 规矩是「**员工不碰主树，只返回 diff**」。
所以 `out` 里声明了 `patch` 的节点，**必须交 diff，不许落盘**。三个字段的语义不能混：

| 字段 | 意思 | 谁判它 |
|---|---|---|
| `patch` | 交给下游的 diff 文本 | 交空 → `patch_missing`（下游会拿到「上游没有给这个字段」） |
| `patch_targets` | 这个 patch **会**改到的文件（数组） | 跑出允许清单 → `patch_out_of_scope`（落地时一样会越界） |
| `changed` | 你**本次实际落盘**改了哪些文件 | 与磁盘快照对撞（overclaim / underclaim） |

⚠️ **把 patch 目标填进 `changed` 会被判 `overclaim`** —— 那是下游落地之后才会发生的变化，不是你改的。
⚠️ 声明了 `patch` 却直接写主树 → `fs_write_forbidden`（**比只交 patch 更容易失败，不是更容易过**）。
2026-11-09 之前正好相反：照设计做的节点被判 overclaim 失败，违规写主树的反而判 ok —— **奖励违规、惩罚守规矩**。

**三条硬要求（都是整图拒收）：**
- `out` 里有 `patch` ⟹ **`kind` 必须是 `work`**。（`PATCH_REQUIRES_WORK`）
  只读 kind 交不出 diff；`integrate` 本来就要落盘，声明 patch 就永远满足不了自己。
- `out` 里有 `patch` ⟹ **`out` 里必须有 `patch_targets`**。（`PATCH_WITHOUT_TARGETS`）
  没有它 `patch_out_of_scope` 就没有输入 —— 那是一条**永远不响**的判据。
- `patch` 节点上的 `accept.cmd` 只 warn（`PATCH_CMD_ON_MAIN_TREE`）：命令在**主树**上跑，
  看不见自己的 patch，验的是别的东西。可执行验收挪到 `integrate`，或改用 `assert`。

## 六、预算（F2 的血泪）

24. **`budget.max_calls` 是「最多调几次 agent」，含重试与升档，不是「起几个 agent」。**
    别按节点数填。（旧名 `max_agents` 现在直接拒收：`BUDGET_RENAMED`）
25. **节点数 > `max_calls` → 整图拒收**：连每个节点的第一次调用都发不完。（`BUDGET_OVERFLOW`）
    填法：`max_calls >= 节点数`，建议直接给到最坏情况（见第 26 条）。
26. **最坏情况 > `max_calls` 只 warn 不拒收**（`BUDGET_NO_MARGIN`）。含义是：跑到后面时重试/升档会被拒。
    执行器给每个节点**预留下一次首发名额**，重试和升档只能吃余量 —— 所以「一次重试把末节点挤掉」不会再发生，
    但你也别指望重试一定生效。最坏情况 = `Σ(1 + on_fail.retry + 声明了 escalate ? 1 : 0)`。

## 七、失败处理

27. 每个节点都要写 `on_fail`。`retry` 是非负整数（≤3），`escalate` 是档位名，`then` 是
    `replan` / `skip` / `abort` 之一。（`NODE_ON_FAIL`）
    **可用档位名有五个：`scout` / `work` / `integrate` / `check` / `strong`** —— 与 `tier` 同一张表。
    `strong` 档 2026-11-12 移除过一次（`V4PRO-REMOVE`，当时它指 deepseek-v4-pro），
    **2026-11-23 以另一个含义加回**（`DAG-MIXED-MODEL`）：它现在指**订阅通道的 GPT-6.2-Astra**，
    经 `codex-gw` 路由 + `--patch` overlay 送进请求层，不是 DeepSeek 的模型。
    另外默认档变了：`contract` / `integrate` / `check` / `replan` 现在**默认落 `strong`**（判断型），
    `scout` / `work` 仍走 deepseek-flash（吞吐型）。两处档位表（`dag.workflow.mjs` 的 `TIERS`
    与 `runner.py` 的 `TIERS`）必须逐字一致，`tier-drift.py` 核这个。
28. 不写 `on_fail` 不拒收，但会打 warn —— **失败是静默的**，不声明就会被悄悄吞进汇总。

## 八、只是警告（不拒收，但会被记下来）

- `WHEN_BRITTLE`：对 `summary` / `verdict` / `facts` / `patch` 这类**自由文本**字段做 `eq` / `in` 分支。
  模型换个说法分支就断，且是**静默**断的。能选可枚举的词就选。
- `CHECK_CMD_UNVERIFIED`：`check` 节点里有 `cmd` —— Gate 判不了这条命令的副作用（会不会写文件）。
  跑之前自己核一遍：核查员跑的命令不该改变任何文件。
- `CHECK_NO_EXECUTABLE`：`check` 节点里**一个 `cmd` 都没有**，核查结论全靠 `assert` / `review` 自评。
  别为了躲上一条 warn 就把命令删光 —— 那会让核查变成模型自己给自己打分。给一条真正只读的命令。
- `BUDGET_NO_MARGIN`：见第 26 条。
- `BUDGET_USD`：`budget.max_usd` 无法核（引擎不回传 cost），只数 agent 次数。
- `CMD_NO_EXEC_BYPASS`：`accept` 里是 `powershell … -File xxx.ps1` 但没有 `-ExecutionPolicy Bypass`。
  本机的执行策略会把脚本拦在 `is not digitally signed` → 命令必败，而 **Gate 不会替你跑一遍**。
  写成 `powershell -NoProfile -ExecutionPolicy Bypass -File xxx.ps1`。2026-11-09 真跑里 `integ` 三轮
  全败的唯一共同原因就是它（落盘全做对了，验收命令跑不动）。
- `FROM_OPTIONAL_UPSTREAM`：本节点 `join=any`（允许上游失败），但 `ctx.from` 又硬引用某个上游的字段。
  上游一失败那个字段就是空的 —— 执行器会落 `need_info` 停住（**不会**再自己发挥顶替），这片下游就整片停。
  要么改 `join=all`，要么别引用失败上游的产出。
- `NODE_ON_FAIL`：没声明 `on_fail`。
- `GRAPH_PLAN_ID`：缺 `plan_id`。

## 九、怎么判「这张图好不好」

Gate 只判**合不合法**，不判**好不好**。好不好看三件事：

1. **并行度**：互不依赖的节点有没有真的并行起来？`waves` 里每波有多个节点才叫并行。
2. **上下文干净**：每个节点的 `ctx.read` / `ctx.from` 是不是只给了它需要的？给了整包就是浪费。
3. **验收可执行**：`accept` 里的命令真跑起来能不能判对错？还是只是「看起来像验收」？

---

### 附：只读核查员（`check`）的四条自查

- 不写文件（`writes` 必须是空或不存在）。
- **不要复用写手跑过的同一条命令**：那条命令在写手那儿会改文件，在这里不会因为换了节点就变成只读。
  要复核就换一条真正只读的：`diff`、哈希比对、只读统计。（`CHECK_REUSES_WRITER_CMD`）
- **也不要因此把命令全删光**：只剩 `assert` 的话，核查就变成模型自评。（`CHECK_NO_EXECUTABLE` 会提醒）
- `cmd` 会在无人工干预的流水线上被执行，副作用只能自己保证。

---

## 十、只登记、不生效的三个字段（2026-11-23 · `DAG-MIXED-MODEL`）

这三个字段**这次只写进 `manager.schema.json` 与本文档，runner 一行执行逻辑都没加**。
Gate 也**不校验**它们的内容 —— 写错了不会拒收，也不会生效。别把它们当能力用。

| 字段 | 写在哪 | 现在的真实含义 |
|---|---|---|
| `nodes[].tools` | 节点 | 声明「本节点可用哪些生成工具 id」。**纯占位**：没有任何执行器读它，写了也不会有工具被挂上来 |
| `out` 里的 `artifacts` | 节点的 `out` | 仍是**字符串数组**（兼容既有图）。将来允许元素为 `{path, kind}`，`kind ∈ image｜audio｜video｜text`，用来给生成类产物标类型 |
| `accept[].target` | 验收条 | 这条 `review` 断言由谁执行。**取值锁死在 `human`**：伙伴 2026-11-23 决定「现阶段只验收代码」，多模态自动验收不实现 |

**为什么先占位**：生成模型（生图 / 生视频 / 生音乐）将来必须以「**工具**」身份进图，不以「引擎」身份 ——
引擎决定沙箱、只读执法、并发资格，一旦按节点选引擎，`compare()` 的四类判定与 `attribution="wave"` 的
连坐归因全都要重写（详见 `tasks/DAG-MIXED-MODEL-2026-11-23/DESIGN.md` §3.3 / §3.4）。

**还有一个前置判断**（别跳过）：生成类任务进 DAG 之前先问「**需要 agent 判断吗**」——
需要（按分镜决定生成什么、按反馈调参数）才进 `work` 节点；不需要（纯批量出片）就用独立脚本，
别塞进 DAG：DAG 换不来任何判据，只多一层开销。
