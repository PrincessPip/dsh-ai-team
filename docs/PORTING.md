# 移植指南

> **先说结论**：`engine/` 是**实战原样**迁过来的，不是跨平台重写。
> 它在 Windows + PowerShell + 特定后端（codex CLI / 本地网关）上跑过几百轮真活，
> 但那套环境假设**一行都没改**。这份文档把每一处绑定列到 `文件:行`，并给出改动点与难度。
>
> 难度评级：**易**（改常量 / 换个路径）· **中**（改调用方式，保留逻辑）· **难**（要换实现，逻辑等价但代码得重写）· **极难**（依赖的东西不在本仓库里）。

---

## 0. 移植地图

| # | 绑定类别 | 硬点在哪 | 移植要改什么 | 难度 |
|---|---|---|---|---|
| 1 | **Windows Job Object**（子进程树清理） | `engine/review-snapshot.py:136` `:140` `:150` `:173` `:176` | 整块换成 POSIX 的 `setsid` / `process_group` + `killpg`，`os.name` 分支必须删 | **难** |
| 2 | **PowerShell 脚本本体**（6 个 `.ps1`） | `manager.ps1` / `ledger-audit.ps1` / `ledger-check.ps1` / `ledger-check-test.ps1` / `review-backfill.ps1` / `review-index.ps1` | 用 Python / Node 重写；**没有等价物现成可抄** | **难** |
| 3 | **Python → `powershell` 调用点** | `manager.py:150-151` · `review-codex.py:88` · `fire-chief.py:527-528` · `runner.py:338-339` | 换成 `pwsh`（PowerShell 7）或直接改用第 2 条的重写产物 | **中** |
| 4 | **执行策略规则**（Gate 内建） | `dag.workflow.mjs:340` `:347` `:349`（`CMD_NO_EXEC_BYPASS`） | 按平台分支：非 Windows 上这条规则要么删、要么改成「目标平台真的有这个约束吗」 | **中** |
| 5 | **CP936 / BOM 编码假设** | `review-codex.py:29` · `runner.py:218` · `dag-gate.mjs:39-40` · `fire-chief.py:498` | 统一 UTF-8 后可删掉大部分；BOM 剥离留着无害 | **易** |
| 6 | **codex CLI 路径与 argv** | `review-codex.py:70-81` `:172-181` · `fire-chief.py:396-409` `:456-469` · `runner.py:108` | 装 codex CLI 到 PATH 或改 `CODEX_JS` / `codexPath`；`--approve-for-me` 的语义要重核 | **中** |
| 7 | **`codex-gw` 本地网关**（判断型档位） | `runner.py:141-145` · `dag.workflow.mjs:45-48` | 没有网关就改 `TIERS.strong` 指向你能拿到的 provider | **难** |
| 8 | **egress 自检脚本**（隧道出站） | `fire-chief.py:509-510` · `manager.py:131` · `review-codex.py:58` · `runner.py:110` | **这些脚本不在本仓库**（私有运维件）。要么实现自己的前置检查，要么把这两处硬拦去掉并**在文档里写明降级** | **极难** |
| 9 | **硬编码 `~/.dsh` 路径** | 见 §4 表格（9 处） | 改成配置项或环境变量 | **中** |
| 10 | **目录布局假设 `ROOT = HERE.parent`** | 见 §5 表格（11 处） | 引擎原本住在 `<project>/scripts/`，本仓库在 `engine/`；跑之前必须决定用哪种布局 | **易** |
| 11 | **模型名写死** | `review-codex.py:59` · `fire-chief.py:1016` · `manager.py:351` · `runner.py:116` · 两张 `TIERS` 表 | 改常量；注意**两张档位表必须同时改** | **易** |
| 12 | **脱敏占位符落在代码里** | `fire-chief.py:447` | 见 §6，**本仓库已修复**（保留为案例） | **易** |
| 13 | **仓库根出现指向依赖树的 junction/链接** | `runner.py:706-735`（`snapshot()` 的 `root.rglob("*")`） | 跑前把链接移出仓库；或用不产生链接的依赖安装方式。见 §5.4 | **易**（但踩上会很慢） |

---

## 1. Windows + PowerShell

### 1.1 子进程树清理：用的是 Windows Job Object，不是 `taskkill`

`engine/` 里**一处 `taskkill` 都没有**（实测全目录大小写不敏感搜索为 0 命中）。
「杀干净孙进程」这件事在送审路径上是靠 **Windows Job Object** 实现的：

| 位置 | 做了什么 |
|---|---|
| `engine/review-snapshot.py:136` | `class WindowsJob` —— 「Suspend before assignment so descendants cannot escape the launch race」 |
| `engine/review-snapshot.py:140` | `ctypes.WinDLL('kernel32', use_last_error=True)` |
| `engine/review-snapshot.py:147-151` | `AssignProcessToJobObject` + `ntdll!NtResumeProcess`（先挂起再入 Job，再恢复） |
| `engine/review-snapshot.py:152-165` | `TerminateJobObject` + 轮询 `QueryInformationJobObject` 确认归零 |
| `engine/review-snapshot.py:173` | **`if os.name != 'nt': raise OSError('Windows Job Object required on this implementation')`** |
| `engine/review-snapshot.py:176` | `creationflags=0x4`（`CREATE_SUSPENDED`）—— 只有 Windows 有 |

> **移植要改哪里**：整块替换。POSIX 上的等价做法是 `Popen(..., start_new_session=True)` +
> 超时后 `os.killpg(os.getpgid(p.pid), SIGKILL)`，然后**轮询确认 pgid 下无存活进程**再算清理成功。
> 注意 `review-snapshot.py:173` 那句硬 `raise` 必须一起删，否则非 Windows 上送审会**直接失败在这一行**。

其余路径（不涉及送审）只有 `p.kill()`，**不杀进程树**：

| 位置 | 现状 | 后果 |
|---|---|---|
| `engine/fire-chief.py:635-646` | `Popen` + 超时 `p.kill()` | Windows 上孙进程会残留（codex 会起 node 子进程） |
| `engine/review-snapshot.py:191-196` | `p.kill()` 仅作为 Job Object 失败时的兜底 | 同上，但有 Job 兜着 |

> **移植要改哪里**：这两个点按上面的 `killpg` 方案一起改。**别指望「超时了进程自然就退了」** ——
> 残留的孙进程会继续写产物目录，把下一轮的证据判成「被污染」。

### 1.2 六个 PowerShell 脚本

| 脚本 | 干什么 | 谁调它 |
|---|---|---|
| `engine/manager.ps1` | 经理层的**可选薄壳**：先跑出站自检，再转 `manager.py` | 人（可选） |
| `engine/ledger-audit.ps1` | 账本对账：文件 mtime ↔ 账本行，找「改过但没记账」 | `review-codex.py` 的前置链、人 |
| `engine/ledger-check.ps1` | 账本**格式**检查（七种形态 + baseline） | `ledger-check-test.ps1`、人 |
| `engine/ledger-check-test.ps1` | `ledger-check.ps1` 的断言台（30 例） | 改账本检查器时跑 |
| `engine/review-backfill.ps1` | 从验收官原始文本里抽回裁决 JSON | 送审之后 |
| `engine/review-index.ps1` | 生成 `decisions/INDEX.md` 索引 | 人（`-Write` 才落盘） |

`ledger-check-test.ps1` 里还有两处**硬编码 Windows PowerShell 可执行文件**：

| 位置 | 内容 |
|---|---|
| `engine/ledger-check-test.ps1:46` | `$engine = Join-Path $PSHOME 'powershell.exe'` |
| `engine/ledger-check-test.ps1:48` | `Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'` |

> **移植要改哪里**：除 `manager.ps1` 这个薄壳外，**其余五个脚本没有跨平台等价物** —— 用 Python / Node 重写。
> 重写时能直接抄的是**它们的判据规则**（七种账本形态、baseline 语义、mtime 对账口径），
> 规则本身在 `docs/TRIGGER-POLICY.md` §二 里写得很细，不用去逆向 `.ps1`。

### 1.3 Python 调 PowerShell 的四个点

| 位置 | 形态 |
|---|---|
| `engine/manager.py:150-151` | `subprocess.run(["powershell", "-NoProfile", "-File", str(script)], ...)` |
| `engine/review-codex.py:88` | 同上（egress 自检） |
| `engine/fire-chief.py:527-528` | 同上（egress 自检） |
| `engine/runner.py:338-339` | `["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(p), "-Json"]` |

> **移植要改哪里**：最省事是把字面量 `"powershell"` 抽成一个配置项（`--powershell` / `PWSH` 环境变量），
> 指向 `pwsh`。注意 `runner.py:339` 用了 `-ExecutionPolicy Bypass`，`pwsh` 7 上这个参数仍接受，但语义已不同。
> 更彻底的做法是把 §1.2 的脚本重写掉，这四处一起消失。

### 1.4 执行策略规则（Gate 内建）

| 位置 | 内容 |
|---|---|
| `engine/dag.workflow.mjs:340` | 注释：「Windows 上 `powershell -File x.ps1` 会被执行策略挡住」 |
| `engine/dag.workflow.mjs:347` | 正则识别 `pwsh` / `powershell` |
| `engine/dag.workflow.mjs:349` | `warn('CMD_NO_EXEC_BYPASS', ...)`：写成 `powershell -NoProfile -ExecutionPolicy Bypass -File ...` |
| `engine/graph-rules.md:130-132` | 同一条规则的人读说明 |

> **移植要改哪里**：这条规则是**平台专属的护栏**（它保护的是「验收命令必败」这个坑）。
> 移植到 Linux / macOS 后它要么删掉，要么改成对目标平台真的成立的前置要求。
> **别留着它当装饰** —— 一条永远不触发的规则会让后面的人以为「这条路径有守护」。

### 1.5 编码：CP936 与 BOM

| 位置 | 现象 |
|---|---|
| `engine/review-codex.py:29` | 注释：PowerShell 的 `>` 会按 CP936 转码，中文报告会坏 → 所以报告必须**由命令直接落盘** |
| `engine/runner.py:218` | Windows 上 Python stdout 默认走 locale 编码（cp936），下管道中文会乱 → 显式 UTF-8 对齐 |
| `engine/dag-gate.mjs:39-40` | 剥 BOM：Windows 编辑器另存 UTF-8 带签名时 `JSON.parse` 直接失败 |
| `engine/fire-chief.py:498` | 给外部执行体写死的中文读法（`Get-Content -Encoding UTF8` / `[IO.File]::ReadAllLines(..., UTF8)`） |
| `docs/INC-TEMPLATE.md:15-19` | 同一件事写给救火队长看 |

> **移植要改哪里**：目标平台本来就是 UTF-8 时，这些防御多半变成无害的历史包袱（BOM 剥离建议留着）。
> 但 `review-codex.py:29` 背后的**纪律**不能丢：**证据文件必须由命令直接写，不许经手抄 / 不经 shell 重定向**。
> 手抄会转码、会「先写结论后做事」——这条在任何平台上都成立。

---

## 2. codex CLI 依赖

### 2.1 谁在调 codex，怎么调

| 位置 | 内容 |
|---|---|
| `engine/review-codex.py:70-81` | `find_codex()`：先看 `CODEX_JS` 环境变量，否则 `~/.dsh/profiles/web/node_modules/@openai/codex/bin/codex.js`；**找不到就 `SystemExit`，不退化到 PATH** |
| `engine/review-codex.py:172-181` | argv：`node <codex.js> exec --json --skip-git-repo-check -m <model> -c model_reasoning_effort=<effort> --approve-for-me -C <root> -`（**prompt 走 stdin**） |
| `engine/fire-chief.py:396-409` | `find_codex()`（同样 `CODEX_JS` 优先 + 包内路径） |
| `engine/fire-chief.py:456-469` | `build_argv()`：`--json` / `--skip-git-repo-check` / `-m` / `-c model_reasoning_effort=` / `--approve-for-me` / `-C` / **prompt 走 argv** |
| `engine/fire-chief.py:1009` | `--engine claude` **显式拒绝**，不静默降级 |
| `engine/runner.py:102-119` | `CODEX_BIN` / `CODEX_EGRESS` / `CODEX_WORKER_MODEL`；注释说明「codex CLI 不在 PATH，只能按包路径定位，找不到就 die(3)」 |
| `engine/runner.py:1453-1467` | codex 引擎的员工 argv（注释里写明「`-s read-only` 一个字都不要加回来」） |
| `engine/codex-trace-patch.py:35-36` | `PLUGIN_LIB = ~/.dsh/profiles/web/node_modules/@deepseek-ai/dsh-subagent-codex/lib` —— 给 DSH 插件打「工具轨迹回传」补丁 |

**两个相反的 prompt 传递方式**（不是笔误，各有原因）：

- `review-codex.py` 走 **stdin**：注释说「含空格 / 中文的 prompt 用 argv 传会被拆」。
- `fire-chief.py` 走 **argv**：注释说「实测中文完好」。

> **移植要改哪里**：先在你自己的环境上重测这两条，**别照抄结论** —— 它们是同一个人在两台不同 codex 版本上的实测，
> 很可能与 CLI 版本相关。改法是二选一后统一，不要一半 stdin 一半 argv。

### 2.2 `--approve-for-me` 与「只读」的真实语义

这是**最容易被误读的一处**，几份文档都在反复纠正它：

| 事实 | 出处 |
|---|---|
| Windows 上 codex 的 `-s read-only` 实测是「**禁止执行任何命令**」，连 `Get-Content` 都被 `blocked by policy` | `engine/review-snapshot.py` 无关；见 `engine/runner.py:161-169`、`engine/review-codex.py:10-17` |
| 所以验收官只能拿 `--approve-for-me`（= `workspace-write` + `auto_review`） | `engine/review-codex.py:178` |
| **只读降级为「行为约定 + 事后快照核」**，裁决报告必须写 `readonly_enforcement: snapshot_only` | `engine/review-codex.py:15`、`engine/review-codex.py:108` |
| `-s` 取值 `read-only` / `workspace-write` / `danger-full-access` 与 DSH 的 `DSH_PERMISSION_MODE` **同名同义**；`never` / `approve-for-me` / `dangerously-bypass` 是**审批策略**那一维，不是沙箱档 | `engine/runner.py:53-55` |
| `codex` 引擎的只读节点拿的是 `workspace-write`，只读约束**退到事后快照核** | `engine/runner.py:60-63`、`RUN.json.readonly_enforcement` |

> **移植要改哪里**：换成任何别的后端时，**这条降级必须照样出现在证据里**。
> 「我给它只读权限了」不是结论；结论是「事后快照核过，且我知道它查不出树外写入」。

### 2.3 模型目录按 CLI 版本下发（版本门槛）

`engine/runner.py:111-116` 的注释写死了这个坑：**codex CLI 的可用模型随版本下发**，
旧版本（`0.153.4`）的模型目录里根本没有 6.x 系列，请求会 `400 model_not_found`；
升到 `0.167.x` 后同账号同登录态直接可用。

> **移植要改哪里**：`CODEX_WORKER_MODEL`（`runner.py:116`）与各处 `DEFAULT_MODEL`（§7）**必须与你实际装到的 CLI 版本对得上**。
> 冒烟一次：用你写死的模型名跑一个最小请求，看到正常出字再往下走。

---

## 3. `codex-gw` 本地网关（判断型档位）

| 位置 | 内容 |
|---|---|
| `engine/runner.py:141-145` | `TIERS.strong = { provider: "codex-gw", model: "<订阅通道模型>" }` —— 经**本地网关**转发到订阅 |
| `engine/runner.py:117-119` | provider 解析需要环境变量 `CODEX_GW_KEY` 存在（`apiKeyEnv`），网关**不校验入站凭据**，所以占位值无权限 |
| `engine/dag.workflow.mjs:42-48` | 同一档位的 Gate 侧定义 + 一条**明确的边界**：workflow 解释器里到不了它（换模型要 runner 的 `--patch` overlay），所以本档在 Gate 里只是「记录用」 |
| `engine/runner.py:1533-1549` | headless 下用 `--patch` overlay 换 `agent-default-model`，并注入 `CODEX_GW_KEY` 与代理 |

> **这是本仓库最不「开箱即用」的一块**：网关本体**不在仓库里**，配置也不在。
> **移植要改哪里**：三选一 ——
> 1. 不移植：把 `TIERS.strong` 指到一个你能拿到的 provider（任意 OpenAI 兼容端点），判断型节点照样能跑；
> 2. 自己起一个把订阅暴露成 OpenAI 兼容 API 的网关，再把 `provider` / 端口 / `apiKeyEnv` 对齐；
> 3. 保留两档但都用同一个模型（**会丢掉「判断型走强模型」的成本设计**，要在文档里写明）。

---

## 4. 出站自检（egress）：最大的一个洞

`engine/` 里有 **5 处**依赖 `~/.dsh/scripts/cdp/` 下的出站自检脚本 —— **这些脚本不在本仓库**（私有运维件，见 `reports/REDACTION-REPORT.md` 的「未公开文件及原因」）：

| 位置 | 指向 |
|---|---|
| `engine/manager.py:131` | `~/.dsh/scripts/cdp/codex-egress-check.ps1` |
| `engine/review-codex.py:58` | 同上 |
| `engine/fire-chief.py:509-510` | `codex-egress-check.ps1` / `claude-egress-check.ps1` |
| `engine/runner.py:110` | `codex-egress-check.ps1` |
| `engine/manager.ps1:56`、`:67` | `$env:USERPROFILE\.dsh\scripts\cdp\claude-egress-check.ps1` |

判据是**双重**的，两条都要满足才算过（`engine/review-codex.py:92-95`、`engine/manager.py:158-167`）：

1. 退出码 `0`；
2. 输出里有一行同时含 `[result]` 与 `READY`。

> 为什么双重：「只看退出码」会被一个 `exit 0` 的空脚本绕过。
>
> **移植要改哪里**：这些脚本承担的是「这一枪打的是哪个出口」的检查 —— 对普通用户来说大概率**根本不需要**（没有隧道就没有这回事）。
> 所以改法是：把这两处硬拦替换成**你自己的前置条件检查**（例如「后端凭据可用」），
> 或者干脆删掉，但**必须在 README / PORTING 里写明这一层降级了**。
> ⚠️ 特别提醒：`review-codex.py:85` 的注释写着「**硬拦，没有跳过开关**」。
> 如果你删了它，就与这条设计承诺冲突 —— 要么加一个显式的 `--no-egress` 并打印警告，要么别删。

---

## 5. 硬编码路径与目录布局

### 5.1 `~/.dsh` 下的绝对路径（9 处）

| 位置 | 路径 |
|---|---|
| `engine/runner.py:108` | `~/.dsh/profiles/web/node_modules/@deepseek-ai/dsh-subagent-codex/...` |
| `engine/runner.py:110` | `~/.dsh/scripts/cdp/codex-egress-check.ps1` |
| `engine/runner.py:258-261` | `%APPDATA%/npm/node_modules/@deepseek-ai/dsh/lib/bin.js`，**外加一条 `Path.home()/"AppData"/"Roaming"/...` 的字面量兜底** |
| `engine/runner.py:376` | `~/.dsh/.credentials.yaml` |
| `engine/manager.py:84`、`:131` | codex 包目录、egress 脚本 |
| `engine/review-codex.py:58`、`:77` | egress 脚本、codex.js |
| `engine/fire-chief.py:406`、`:509-510` | codex.js、两个 egress 脚本 |
| `engine/codex-trace-patch.py:35-36` | DSH 插件 lib 目录（`HOME = Path.home()`） |

> **移植要改哪里**：抽一个 `--dsh-home` / `DSH_HOME` 解析函数，全仓统一。
> `runner.py:261` 那条 `AppData/Roaming` 字面量是最脏的一处（它假设了 Windows 的家目录布局），优先删。

### 5.2 临时区与外部审计根

| 位置 | 现状 |
|---|---|
| `engine/runner.py:937`、`:952-954` | 员工临时区默认 `%LOCALAPPDATA%\ai-team-sandbox`（**在 workspace 外**，因为 dsh 的 Windows ACL 沙箱硬拒 workspace 内的 temp root） |
| `engine/runner.py:1851` | 同上的命令行参数默认值 |
| `engine/runner.py:1054-1060` | 逃逸指纹看 `USERPROFILE` / `TEMP` / `LOCALAPPDATA` 三个根的顶层新增项 |
| `engine/agents-budget.py:53-56` | `%APPDATA%/npm/...` 与一条 unix 风格候选（两处都试） |
| `engine/constraints-mutate.py:185` | 隔离跑时重定向 `TEMP` / `TMP` / `TMPDIR` / `LOCALAPPDATA` |
| `engine/ledger-external.txt:1-6` | 外部审计根清单，`~` 展开成 `%USERPROFILE%`（`ledger-audit.ps1:76-82` 实现） |

> **移植要改哪里**：`%LOCALAPPDATA%` → `XDG_CACHE_HOME` 或 `$TMPDIR`。
> **注意这条不能只改字符串**：`runner.py:900-914` 说明这个位置踩过「workspace 内的 temp root 被沙箱拒」的坑，
> 换平台后要重测一遍「临时区真的在 workspace 外，且子进程能写」。

### 5.3 `ROOT = HERE.parent`：引擎原本住在 `scripts/` 下

本仓库把引擎平铺在 `engine/`，但**引擎自己的代码假设它在 `<project>/scripts/`**：

| 位置 | 表达式 |
|---|---|
| `engine/runner.py:97-98` | `HERE = Path(__file__).resolve().parent` / `ROOT = HERE.parent` |
| `engine/manager.py:46-47` | 同上 |
| `engine/review-codex.py:50-51` | 同上 |
| `engine/fire-chief.py:79-80` | 同上 |
| `engine/doc-sync.py:73-74` | 同上 |
| `engine/constraints-check.py:66`、`constraints-mutate.py:35`、`constraints-selftest.py:38` | `ROOT = Path(__file__).resolve().parent.parent` |
| `engine/cite-check.py:44` | `DEFAULT_ROOT = Path(__file__).resolve().parent.parent` |
| `engine/codex-trace-patch.py:42` | `ROOT = Path(__file__).resolve().parents[1]` |

也就是说：**从 `engine/` 直接跑，`ROOT` 会算成仓库根**，于是它去找 `<仓库根>/decisions/`、`<仓库根>/AGENTS.md`、`<仓库根>/runs/` —— 这些目录在本仓库里**不在那个位置**。

> **移植要改哪里**：二选一，**别混着来**：
> - **A**：把 `engine/` 整个复制成目标工程的 `<project>/scripts/`，并按 §5.1 建好 `decisions/`、`runs/`、`tasks/` 等目录；
> - **B**：加一个 `--root` 参数（或环境变量），把 `ROOT` 从「推导」改成「声明」。**推荐 B** —— 推导出来的根永远是猜。

### 5.4 仓库根出现指向依赖树的 junction 时，快照会走进去（实测）

**这是安装方式带来的性能坑，不是功能 bug，但会让每次起跑都明显变慢。**

成因在 `engine/runner.py` 的 `snapshot()`：

| 位置 | 内容 |
|---|---|
| `engine/runner.py:706` | `def snapshot(root)` —— 每次 attempt 都要跑，docstring 写明「每次 attempt 要快照 2～3 次」 |
| `engine/runner.py:718` | `for p in root.rglob("*")` —— **遍历用 rglob，会把目录 junction 当成普通目录走进去** |
| `engine/runner.py:178` | `EXCLUDE_DIRS = {"node_modules", ".git", "runs", "__pycache__", ...}` —— 名字过滤在**循环内部逐条**执行（`:725`），**不是剪枝** |
| `engine/runner.py:719` | 每条都要 `p.is_file()` 判一次，成本照付 |

也就是说：**名字过滤挡得住「进快照」，挡不住「被遍历」**。遍历成本在过滤之前就已经花掉了。

**实测数字**（2026-11-24，本仓库当下的树）：

| 场景 | `rglob("*")` 条目数 | 其中 `node_modules` 下 | 纯遍历耗时 |
|---|---|---|---|
| 仓库根存在指向 DSH 安装树的 `node_modules` junction | **69,208**（文件 63,923） | **68,770** | **2.06 s** |
| 去掉这个 junction 后（估算 = 69,208 − 68,770） | ≈ 438 | 0 | ≈ 0.01 s |

放大 **≈157 倍**。而 `snapshot()` 每次 attempt 要跑 2～3 次 —— 一张 3 节点的图可能跑 6～9 次，
光遍历就白烧十几秒；节点越多、重试越多，浪费越大。**fixture 与真跑都一样中招**（它跟引擎无关）。

> **症状是「慢」，不是「错」**：`EXCLUDE_DIRS` 名字过滤仍然生效，`node_modules` 那些路径**不会进 `out`**，
> 不会污染 `FS-*.json` 与 `diff`。所以别把「跑得慢」误判成「证据被污染了」。

**为什么会踩上**：这跟 §1 / README 里的依赖安装方式直接相关 ——
`npm link`、`pnpm` 在 Windows 上的目录链接、`New-Item -ItemType Junction`，
都会在 `node_modules` 里造出**指向仓库外**的链接。本仓库当下的开发树就是这样：
`node_modules` 是一个 **Junction**，目标指向全局 npm 前缀下 DSH 的 `node_modules`。

> 一个必须知道的区别：**junction 会被 `rglob` 跟进；真符号链接（symlink）不会**
> （`pathlib` 的递归遍历只对 `is_symlink()` 为真的目录跳过，而 Windows junction 的 `is_symlink()` 是 `False`）。
> 这条只在 junction 上实测过（symlink 需要管理员权限创建，本机未能复现）；
> 不确定你手上是哪种链接时，按**最坏情况**处理 —— 当成会跟进。

> **移植要改哪里**：三选一 ——
> 1. **跑前把链接移出仓库**（最省事）：把 `node_modules` 整个移走 / 改名，跑完再放回；
> 2. **改安装方式**：用不产生目录链接的方式提供依赖（§1 的路线 ②：装进 DSH 树，本仓库不出现链接）；
> 3. **改引擎**：在 `snapshot()` 里改成带剪枝的遍历（`os.walk(followlinks=False)` + `dirnames[:]` 过滤），
>    或先判 `os.path.islink` / reparse point 再决定是否递归。⚠️ 改 `runner.py` 必须跑执行器台子，
>    而**那套台子本仓库未随附**（见 §11）—— 所以优先选 1 或 2。

---

## 6. 脱敏占位符曾落在代码里（**本仓库已修复**，保留为案例）

`reports/REDACTION-REPORT.md` 记录脱敏时把真实端口替换成了占位符。绝大多数命中落在**注释与文档**里（无害），
但有 **1 处落在可执行代码里** —— **本仓库已修复**：

```python
# engine/fire-chief.py:447（修复后）
    # 开源适配：推不出本机代理端口时**不覆盖**代理环境变量，让子进程继承现有环境。
    if not port:
        return env
```

**修复前**是 `port = port or "<PROXY-PORT-TUNNEL>"`：当环境里推不出代理端口时，`proxy_env()` 会返回非法 URL
`http://127.0.0.1:<PROXY-PORT-TUNNEL>`，子进程拿到的 `HTTP(S)_PROXY` 是坏的 —— 表现为**莫名其妙连不上**，
而不是明确报错。

> **这个案例值得记住**：脱敏工具会把「写在代码里的默认值」也当成敏感值替换掉。只扫「有没有残留」发现不了这类问题，
> 得问「替换之后这段代码还成立吗」。同样地，移植时改完脱敏产物要跑一次真实调用，不能只看 `grep` 干净。
>
> 顺带：`engine/manager.py:148`、`:363`、`engine/runner.py:336` 的错误提示里也有 `<DSH-SCRIPTS>` 字样（只是文案，不影响运行）。

---

## 7. 模型名与档位表

| 位置 | 内容 |
|---|---|
| `engine/review-codex.py:59` | `DEFAULT_MODEL = "gpt-6.2-astra"` |
| `engine/review-codex.py:60` | `DEFAULT_EFFORT = "high"`（注释：**请求值，非服务端确认值**） |
| `engine/fire-chief.py:1016` | 默认模型（同款订阅通道模型） |
| `engine/fire-chief.py:38-50` | 文件头的引擎对照表 + 2026-11-23 的升级说明 |
| `engine/manager.py:351` | 经理层默认模型 |
| `engine/manager.ps1:32` | `$Model = 'opus'` —— **薄壳里的旧默认**，实际由 Python 侧覆盖 |
| `engine/runner.py:116` | `CODEX_WORKER_MODEL`（codex 引擎的员工模型） |
| `engine/runner.py:134-145` | `TIERS`：`scout` / `work` / `integrate` / `check` → 便宜模型；`strong` → 订阅通道模型 |
| `engine/dag.workflow.mjs:37-53` | Gate 侧的 `TIERS`，注释写明「**逐字对齐** `runner.py`，改一处必须两处一起改」 |
| `engine/manager.schema.json:86`、`:179` | 档位说明与「升档会真的换模型」的判据 |

> **移植要改哪里**：改常量很容易，**难的是保持两张档位表一致**。
> 原工程用 `tier-drift` 检测器核这件事，本仓库**没有随附那个检测器**。
> 所以移植后请自己加一条最小检查：把两张表的 `(tier → provider/model)` 抽出来 diff 一次。

---

## 8. 建议的移植顺序

分三步，每步都有可验收的产物 —— **别想一次搬完**。

### 步 1：让「判图」能跑（最小闭环，半天）

1. 装 Node ≥ 18 + Python ≥ 3。
2. 跑 `node engine/dag-gate.mjs <一张 GRAPH.json>` —— 它**零模型调用**（反证内建：假 agent 一调就抛）。
3. 验收：给一张好图 `ok=true`，给一张坏图 `ok=false` 且 `errs` 里有具体 code。

> 这一步能把 §1.4（执行策略规则）、§5.3（ROOT 假设）、§1.5（BOM）暴露出来，而**不花一分钱额度**。

### 步 2：让「送审」能跑（这是本项目的核心，1–2 天）

1. 修 `engine/review-snapshot.py` 的 Job Object（§1.1）与 `engine/review-snapshot.py:173` 那句硬 `raise`。
2. 决定处理 §4（egress）：替换 / 显式删除 + 文档声明。
3. 跑 `python engine/review-codex.py --brief <你的 REVIEW-BRIEF.md> --dry-run` —— 只打印 argv 与 prompt。
4. 真跑一轮：拿一份**故意有洞**的交付去送审，确认它真的会判 `rework` 并指出那个洞。
5. 验收：`--dry-run` 能出 argv；真跑能落出 `REVIEW-raw.txt` + 裁决 JSON；**故意喂一个空证据的 `pass` 宣告，它必须降级**。

> 第 4 步是**反证**，别省。一个从不判 `rework` 的验收官等于没有 —— 这正是 `E3` 要求的东西。

### 步 3：让「DAG 真跑」能跑（按需，1 天起）

1. 决定 §3（`codex-gw` 网关）怎么办。
2. 先 `--engine fixture` 跑通流程（秒级、零成本），再 `--engine headless` 真跑一个小图。
3. 验收：`RUN.json.engine` 是真引擎；`NODES/*.aN.json` 有每次 attempt 的记录；`DIFF.json` 与 `FS-*.json` 齐全。

> ⚠️ `result=pass` 只代表流程跑通。**产物对不对要另写核验器** —— 本仓库没随附（见 README 诚实边界第 3 条）。

---

## 9. 跑 fixture 前必须做的两件事（否则结论是假的）

这两条不是「注意事项」，是**结论有效性**的前提。不做，你会拿到一个**看起来完全正常**的假结果。

### 9.1 fixture 不幂等：第 2 次跑同一场景会「假失败」

| 事实 | 证据 |
|---|---|
| `honest` 场景把 `tasks/DAG-2026-11-06/fixtures/sandbox/notes.md` 从**3 条基线**写成**4 条** | 写后内容写死在 `scenarios/honest/spec.json` 的 `write_files`：「`… - 第三行 \n - A1 fixture 写入行`」 |
| **第 2 次**跑同一个场景：要写的内容与盘上一模一样 → **零改动** | 写操作幂等，diff 为空 |
| 于是被判 **`overclaim`**（「什么都没改却报 changed」） | 与**真正的** `overclaim` 场景失败字符串完全相同 → 极易误判成「插件判据有 bug」 |

**原工程的对策**：`runner-harness.py` 的 `reset_sandbox()`（`BASE_*` 常量，约 `:625-629`）在**每个场景开跑前**
把 `notes.md` / `OFF-LIMITS.md` / `SKIP-ONLY.md` / `BUDGET.md` 重写回基线。
**本仓库没有随附 `runner-harness.py`**（见 §12），插件也**没有内建**这个重置（行为一致性优先）——
所以重置必须由调用方自己做。

**实测补充：被改动的不止那 4 个文件。** 40 个场景里有 **22 个带 `write_files`**，最常被写的是 `notes.md`
（40 份 spec 里出现 66 次）；`OFF-LIMITS.md` / `BUDGET.md` / `PATCH-DIRECT.md` 也会被写；
更麻烦的是**跨场景留下新文件**：

| 场景 | 对 `sandbox/PATCH-A.md` 做了什么 | 证据 |
|---|---|---|
| `wash` | **新建**它，内容 `attempt1 wrote main tree` | `scenarios/wash/spec.json` 的 `write_files` |
| `timeout` | 它的验收 `cmd` 只有在没超时时才会执行，那个 cmd 会把它写成 `CMD-RAN` | `graphs/g-timeout.json:28` |

于是「先跑 `wash`，再跑 `timeout`」会让 timeout 的盘面前提（`PATCH-A.md` 不该存在 / 不该是 wash 那版内容）
**在开跑前就已经被破坏** —— 失败原因看起来跟 wash 毫无关系，你会去查 timeout 的判据，而问题在上一轮。

> **怎么办：整目录备份 / 还原，别背文件名单。**
>
> ```powershell
> # ① 只做一次：在**第一次跑之前**备份基线
> Copy-Item -Recurse tasks/DAG-2026-11-06/fixtures/sandbox $env:TEMP/fixtures-sandbox-baseline
>
> # ② 每次跑前还原（先删再拷，才能连场景新建的文件一起清掉）
> Remove-Item -Recurse -Force tasks/DAG-2026-11-06/fixtures/sandbox
> Copy-Item -Recurse $env:TEMP/fixtures-sandbox-baseline tasks/DAG-2026-11-06/fixtures/sandbox
> ```
>
> 已经跑过一次、没留备份的：克隆来的副本如果已提交过，`git checkout -- tasks/DAG-2026-11-06/fixtures/sandbox/`
> 能还原；否则按各场景 `spec.json` 的 `write_files` 反推写前内容（它给的是写**后**状态）。

### 9.2 必须独占跑：并发不只是「跑不通」，而是让结论变假

`snapshot()` 把 `runs/` 纳入轻量指纹（`engine/runner.py:178`、`:725-731`），
于是**同一时刻第二个 runner 一起跑，两个 run 会互相把对方写证据包认成 `evidence_write`**。

**实测（2026-11-24，本仓库开发树，撞了两次）**：一个 run 的 attempt 窗口里出现了另一个 run 的
`runs/run-<时间戳>/…` 文件 → 前者 `s_recon` 被判 `evidence_write` → `result FAIL`；
对称地，后者的 `s_recon` 也把前者的 9 个证据文件列成污染。

**危害不是「跑不动」，是「结论是假的」**：`evidence_write` 属于**不可洗**的 harm
（`engine/runner.py:199-201`：不因重试消失），一命中就 `abort`。
你会得到一个**看起来像真失败**的 FAIL —— 它指的那些证据**确实存在**，只是**不是你造成的**。
这比跑不起来更坏：跑不起来你会查，假失败会被人当成「这批交付真有问题」。

> **纪律**：跑之前确认没有别的 runner 在跑（看 `runs/` 最近的 mtime，或跟同机器上的人打个招呼）；
> 撞上了**串行重跑**，**不要去关那条判据** —— 它是「证据不可被污染」的唯一机械保障。

---

## 10. 移植后的最低自检清单

按本项目的规矩，每条都得有**证据**（不是「应该没问题」）：

- [ ] `node engine/dag-gate.mjs` 对好图 / 坏图分别给 `0` / `1`，坏图的 `errs[].code` 能对上 `graph-rules.md`（证据：两次命令输出）
- [ ] `review-codex.py --dry-run` 打印出的 argv 与 prompt 完整，`brief` 路径存在（证据：stdout 落盘）
- [ ] 真跑一轮送审，**故意造一个证据缺失的 `pass`**，确认它被判成 `rework`/`escalate`（证据：裁决 JSON）
- [ ] 超时场景：故意让后端卡住，确认**进程树被清干净**（证据：超时后 `ps` / 进程表输出，不是自报）
- [ ] 两张档位表 diff 为空（证据：抽出 `(tier, provider, model)` 三元组的对比输出）
- [ ] `engine/fire-chief.py:447` 的占位符已处理（本仓库已修；证据：该行已是 `if not port: return env`，`grep "<PROXY-PORT-TUNNEL>"` 只剩注释与文档）
- [ ] **跑 fixture 前已还原 sandbox**（证据：还原命令输出 + `fixtures/sandbox/` 下无 `PATCH-A.md`）
- [ ] **跑的时候是独占的**（证据：`runs/` 里没有第二个 run 目录在同期写入；失败若含 `evidence_write`，先排除并发再判死）
- [ ] 若仓库根有 `node_modules` 链接，确认起跑快照耗时没被放大（证据：`rglob` 条目数 / `snapshot` 前后耗时，见 §5.4）
- [ ] 全文搜一遍你改过的文件，确认没有把**你的私有环境**（路径 / 端口 / 凭据）写进去

---

## 11. 台子（改判据之前必须跑的断言机 / 变异台）

这是「判据的守护」——**改判据不跑台子，等于改完没人知道它还判不判**。本仓库随附了三组：

| 台子 | 守护谁 | 怎么跑 |
|---|---|---|
| `engine/cite-check-harness.py` + `engine/cite-check-mutate.py` | 引用锚定 | `python engine/cite-check-harness.py`；变异台 `void>0` 直接判失败（「没变异」≠「抓不到」） |
| `engine/constraints-selftest.py` + `engine/constraints-mutate.py` | 出处制 | 同上 |
| `engine/ledger-check-test.ps1` | 账本格式（30 例 / 11 格） | `powershell -NoProfile -ExecutionPolicy Bypass -File ...` |

**没有随附的台子**（原工程有，本仓库没有 —— 移植时你要自己补，或者别动对应的判据）：

| 缺的台子 | 守护谁 | 后果 |
|---|---|---|
| `runner-harness.py` / `runner-mutate.py` | 执行器（338 条 + 32 变体） | **改 `runner.py` 后无法自证** |
| `manager-harness.mjs` / `verify-findings.mjs` | 经理层 / 坏图反例 | 改出图提示词后无法自证 |
| `tier-drift` 检测器 | 两张档位表的一致性 | 只能靠人工 diff（见 §7） |
| `verify-product.py` | 产物核验 | 流程 pass ≠ 产物 pass（见 README 诚实边界第 3 条） |

> **判断准则**：随附了台子的判据 → 可以改，改完跑台子；
> 没随附台子的判据 → **改之前先补台子**，否则你就在用「我觉得对」替换「机器能判」——
> 那正好是这套东西存在的反面。

---

## 12. 不建议移植的部分（诚实版）

| 部分 | 为什么别碰 |
|---|---|
| `engine/constraints-*`（出处闸门，4 个文件 / 约 135 KB） | 它那套「三态分类 + 豁免条目 + 三处逐字一致」是为一个特定文档体系调的，换一套文档体系必须重调判据。台子随附了（见 §11），但**判据本身要重写**，成本比收益高 |
| `engine/ledger-check-test.ps1` | 断言台离开 `ledger-check.ps1` 没有意义；两者要一起重写 |
| `engine/selfcheck.workflow.mjs` | 它是**交给 DSH `workflow` 工具**的脚本（当 `workflow.script` 传），不是能独立 `node` 跑的程序 |
| `engine/codex-trace-patch.py` | 它给某个具体版本的 DSH 插件打补丁；插件一升级就漂。要用就重写，别移植 |
| `engine/verify-product.py` | **本仓库没有**。产物核验器是按每个交付单元现写的，见 README 诚实边界第 3 条 |
