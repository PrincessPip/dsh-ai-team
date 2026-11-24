# 调度员手册（Lead Playbook）

> 给被降为**调度员**的 Lead：只判断、派活、核对，不亲自实现。
> 协作纪律（三态裁决、`E0`–`E3` 证据强度、`writeScopes`、送审清单）见同目录 [`AGENTS.snippet.md`](AGENTS.snippet.md)，本文不重复；只答：**该不该派**、**怎么派清楚**、**派完怎么核**。

## 一、直办还是派活：三问判据

**默认派，直办是例外。** 产出物有「文件 / 提交 / 服务状态」任一，就是实现类任务：

| # | 可核判据 | 是 → | 否 → |
|---|---|---|---|
| 1 | 产出物只有结论，无文件被创建/修改/删除？`git status --porcelain` 前后一致 | 问 2 | **派活** |
| 2 | 不跑改变状态的命令（写盘、启停服务、装包、改远端、`git push`）？ | 问 3 | **派活** |
| 3 | 一轮只读搜索/命令就能答完，不需独立复核？ | **直办** | **派活** |

命中任一 → **必须派**：送审清单（见 [`AGENTS.snippet.md`](AGENTS.snippet.md)）、需要并行读算、需要拍板的边界
（钱/权限/发布/删数据/口味，写进 brief，**不许自己决定再汇报**）。

## 二、brief 四段（缺一段退回重写）

派活只认一份 brief（`ai_team_plan` 的 `brief`，正文或 `.md` 路径）；缺段会让经理层猜路径，Gate 拦不住猜错：

```markdown
## 目标
把 X 做到 Y（一句话，可判定成败）。
## 边界
不动 A/B/C；不做 D；要拍板的写「待决策：…」，不要自己选。
## writes（允许改哪些文件）
改：src/x.js（只改 f）、docs/y.md（只加一节）；新增：templates/z.md（只交 patch，由 integrate 落盘）
## 判据
- `node --check src/x.js` exit 0
- `rg -c '^## ' templates/z.md` ≥ 4；该文件 5–7 KB、UTF-8 无 BOM
```

**写集纪律（与 Gate 同口径）**：非祖先节点不许写同一文件（`WRITE_CONFLICT`）；并行时 worker 只交 diff（`patch` + `patch_targets`，`changed: []`），唯一 `integrate` 落盘。

## 三、plan → gate → run 的交接

| 步 | 工具 | 输入 | 输出 / 落点 | 交接判据 |
|---|---|---|---|---|
| 出图 | `ai_team_plan` | `brief`（必填）；`outDir`/`model`/`dryRun` 可选 | `{ graphPath, runId, nodeCount, summary }`，落 `runs/<planId>/` 下的 `BRIEF.md`、`GRAPH.json`、`GATE.json`、`RUN.json` | `summary` 含 `Gate OK` → 进 gate；`REJECTED` → 先改图再 gate |
| 判图 | `ai_team_gate` | `graph` = 上一步 `graphPath` | `{ ok, errs[{code,msg}], warns, codes }` | **`ok=true` 才许 run；`ok=false` 必须停** |
| 执行 | `ai_team_run` | `graph`；`engine`（默认 `fixture`）；`runId`；`fixtureDir` 仅 fixture | `{ runId, result, nodes, runDir }`，证据写 `runDir` | **只看 `runDir/RUN.json.result`**；`pass` ≠ 产物正确 |

plan 内部已跑过 Gate（含 1 轮修复），gate 用于手改图后的复核；`ok=true` 只代表图合法。

## 四、真实 Gate 错误码：拒收 → 改图 → 过闸

示例（构造）：`ai_team_gate` 输出（摘录，预期退出码 1；`WRITE_CONFLICT` 是真实错误码）：

```text
[gate] result     REJECTED  (1 error / 0 warn)
  [err ] WRITE_CONFLICT  w_a 与 w_b 写集有交集且互不为祖先，不许偷偷串行：templates/LEAD-PLAYBOOK.md
```

`msg` 开头即节点 id 与冲突路径：两节点都 `after: ["s_recon"]`、都 `writes: ["templates/LEAD-PLAYBOOK.md"]`
⇒ 并行兄弟抢同一文件。只动 `w_b.after`、重跑后预期退出码 0（下方 diff 与输出均为示例（构造））：

```diff
- "after": ["s_recon"],
+ "after": ["s_recon", "w_a"],
```

```text
[gate] result     OK  (0 error / 0 warn)
[gate] nodes      3  waves 3
[gate] critical   s_recon → w_a → w_b  (3 节点)
```

| 常见 code | 定位口径 | 修法 |
|---|---|---|
| `NODE_ACCEPT` | msg 开头节点 id | work/integrate 至少 1 条 `cmd`/`assert`，纯文字拒收 |
| `PATCH_WITHOUT_TARGETS` | 点名交 patch 节点 | `out` 同时声明 `patch` 与 `patch_targets`；只交 patch 的 `changed: []` |
| `SCOPE_EXCLUDED_PATH` | 点名字段与路径 | `runs/`、`node_modules/` 等快照排除区不可写 |

## 五、引擎选择与证据包

| 引擎 | 真调模型？ | 费用 | 对工作树 | 前置 / 边界 |
|---|---|---|---|---|
| `fixture` | 否：假 worker 按 `spec.json` 动作 | 0 | 只在夹具 sandbox 预设写入 | `fixtureDir` **可选**：显式给定时只验该目录的 `spec.json`，省略时依次查 `<cwd>/.ai-team/fixtures`、随附 `scenarios/honest`；含 **FAKE 标记**，**只演练流程**，不能当交付证据；只读节点由沙箱硬拦 |
| `headless` | 是（DSH + DeepSeek 路由） | 按调用计费 | **实际修改工作树**（按节点 `writes`） | 只读节点由沙箱硬拦（`read-only` 写不进去）；缺 dsh → exit 3 |
| `codex` | 是（ChatGPT 订阅的 codex CLI） | 消耗订阅窗口 | 实际修改工作树 | 需 codex CLI + 出口自检 READY，否则 **exit 4 硬拦、无跳过开关**；Windows 的 `-s read-only` 会**禁用命令**，只读节点只能拿 workspace-write，只读约束退到**事后快照核** |

退出码：`0` 全 ok / `1` 有节点失败 / `2` 图或目录冲突 / `3` 缺环境 / `4` egress 未 READY。
`runs/<id>/` 按实际文件名读证据：

| 文件 | 回答什么问题 |
|---|---|
| `GRAPH.json` | 本次执行的**哪张图**（手改后以它为准） |
| `GATE.json` | **Gate 结果**：`ok`/`errs`/`warns`/`waves`/`criticalPath` |
| `PLAN.json` | **执行计划**：`waves`/`parallel_enabled`/`max_parallel`（计划，非结果） |
| `NODES/<id>.json` | 节点最终记录：`status`/`attempt_count`/`harm`/`why`/`result` |
| `NODES/<id>.a<n>.json` | **节点尝试**第 n 次：attempt 窗口、`checks`、`exit_code` —— 判失败原因看这里 |
| `FS-BEFORE.json` / `FS-AFTER.json` | **快照**：起跑前 / 收尾后各一份，路径 → `sha256`/`size`/`mtime_ns` |
| `DIFF.json` | **差异**：`added`/`removed`/`modified`/`touched`/`changed_paths`/`evidence_changes` |
| `RUN.json` | 总档：`engine`/`result`/`nodes`/`waves`/`budget`/`egress`/`readonly_enforcement` |

**必须记死：result=pass 只代表流程跑通，产物对不对要另核。** 独立核验实例（文档类，`verify-product` 未随附、自己写）：

```powershell
# 0) 在工程根跑；$runDir 指实际目录（ai_team_run 的 runDir，或 runs/ 下最近一次完整运行）
$runDir = 'runs/<runId>'   # 例：runs/release-03-rework，改成实际目录
if (-not (Test-Path -LiteralPath "$runDir/DIFF.json")) { throw "runDir 指错：$runDir/DIFF.json 不在" }

# 1) 集合核：自报 changed 必须等于快照差异，对不上 pass 也不认
$diff = Get-Content -Raw -Encoding UTF8 -LiteralPath "$runDir/DIFF.json" | ConvertFrom-Json
$self = @(Get-ChildItem -LiteralPath "$runDir/NODES" -Filter '*.json' | ForEach-Object {
  $n = Get-Content -Raw -Encoding UTF8 -LiteralPath $_.FullName | ConvertFrom-Json
  if ($n.result -and $n.result.changed) { @($n.result.changed) }
} | Where-Object { $_ } | Sort-Object -Unique)
"DIFF：$($diff.changed_paths -join ', ') ／ 自报：$($self -join ', ')"
if (Compare-Object $diff.changed_paths $self) { throw '自报写集 ≠ 快照差异，pass 不认' }
git --no-optional-locks status --porcelain   # 逐行对照起点 status：只许多出预期路径

# 2) 内容核：围栏外二级标题 + 字节数，独立于工人自述
function Test-Doc([string]$Path, [int]$ExpectHeads) {
  $fence = [string][char]96 * 3
  $parts = (Get-Content -Raw -Encoding UTF8 -LiteralPath $Path) -split ('(?m)^' + $fence)
  $heads = 0
  for ($i = 0; $i -lt $parts.Count; $i += 2) { $heads += ([regex]::Matches($parts[$i], '(?m)^## ')).Count }
  $bytes = (Get-Item -LiteralPath $Path).Length
  "围栏外二级标题 $heads 节 / $bytes 字节"
  if ($heads -ne $ExpectHeads) { throw "二级标题 $heads ≠ $ExpectHeads" }
  if ($bytes -lt 5500 -or $bytes -gt 9216) { throw "字节 $bytes 越出 5500–9216" }
}
Test-Doc 'templates/LEAD-PLAYBOOK.md' 6    # 正例：应过
# 3) 反证（E3）：期望值改成不可能的 7，同一判定必须失败
$failed = $false
try { Test-Doc 'templates/LEAD-PLAYBOOK.md' 7 | Out-Null } catch { $failed = $true }
if (-not $failed) { throw '反证未失败：期望 7 也过，说明核对没在核' }
'反证 OK：期望 7 时按预期失败'
```

## 六、什么时候写 `[蜂群例外: 理由]`

只有两种：**① 纯只读**（问答/排查，零文件产出）；**② 规模小到出图+Gate+执行反而更慢**（单文件、单行、可逆，且不属送审清单）。
必须写在**回复开头**；理由必须是**可核事实**（哪次只读操作/哪个文件），不是感受。

- 正例：`[蜂群例外: 纯只读排查，仅读文件与 git log，无写操作]`
- 正例：`[蜂群例外: 只改 .gitignore 一行、不改判据，出图成本高于改动本身]`
- 反例：`[蜂群例外: 这次比较特殊]` —— 无可核事实，按未声明处理。

**不写声明就绕过 = 违规。** 事后核对：回复里有没有声明 + 实际有没有写文件。
