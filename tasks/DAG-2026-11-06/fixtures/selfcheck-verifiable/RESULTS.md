# `verifiable` 四格对照的原始返回体（**四格均已复现**）

> 2026-11-07 第 2 轮验收官判 `rework`，理由是 **C7 / C8 自降 E2 低于「可执行类」的 E3 下限** —— 它说得对。
> 本文件是补证据的结果：**四格现在全部可复现**，不是「我抄下来的数字」。
>
> **重跑方法**（任何人都能做，材料与判据都在盘上）：
> 1. 把 `scripts/selfcheck.workflow.mjs` 的脚本体当 `workflow` 的 `script` 传入；
> 2. `args = { unit, taskPath: ".../TASK.md", deliveryPath: ".../DELIVERY-{clean,hit}.md",
>    files: ["scripts/runner.py", "runs/_harness/retrythenok/RUN.json"], checks: ["verifiable"] }`；
> 3. **旧判据**：把 `CHECKS.verifiable.how` 换成下面 §三·A 的文本再跑；**新判据**：用文件里的原样。

## 一、四格（含复现记录）

| # | 交付说明 | 判据 | `failed_checks` | `issues` | 复现 |
|---|---|---|---|---|---|
| 1 | `DELIVERY-clean.md` | 旧 | **0** | `[]` | ✅ 2026-11-07 重跑（`unit=SELFCHECK-VERIFIABLE-clean-old`） |
| 2 | `DELIVERY-hit.md` | 旧 | **0** ← **该响没响** | `[]` | ✅ 2026-11-07 重跑（`unit=SELFCHECK-VERIFIABLE-hit-old`），**两次独立跑都是 0** |
| 3 | `DELIVERY-hit.md` | 新（文件里当前版本） | **1** | 3 条 blocking（见 §二） | ✅ 可随时重跑（`unit=SELFCHECK-VERIFIABLE-hit-v2`） |
| 4 | `DELIVERY-clean.md` | 新 | **0** | `[]` | ✅ 可随时重跑（`unit=SELFCHECK-VERIFIABLE-clean-v2`） |

读法（这就是 C8 的三个观察维 + 反证）：

- **2 vs 3**：只改判据文本 → `0 → 1`，说明**旧判据几乎不响**（缺口实证）。
- **3 vs 4**：只改证据写法 → `1 → 0`，说明**新判据不是恒报警**（反证）。
- **1 vs 2**：同一旧判据、只改证据写法 → 都是 `0`，进一步确认第 2 格不是偶然。

## 二、第 3 格的完整返回体（原文）

```json
{
  "unit": "SELFCHECK-VERIFIABLE-hit-v2",
  "checks_run": ["verifiable"],
  "checks_missing": [],
  "failed_checks": 1,
  "issues": [
    {
      "check": "verifiable",
      "severity": "blocking",
      "what": "A1「重试确实发生了」只有自述，无任何可核依据：既没有 file:line，也没有可 Read 的产物路径 + 字段名，也没有命令输出原文。",
      "evidence": "tasks/DAG-2026-11-06/fixtures/selfcheck-verifiable/DELIVERY-hit.md:5 原文：「我实测跑了一遍，确实是重试了两次，最终也 ok 了。没问题。」 —— 通篇只有「实测跑了一遍」+「没问题」两类自述。而可引用的硬依据是现成的、交付说明一处未引：runs/_harness/retrythenok/RUN.json:24-30 有 \"w_try\": {\"status\": \"ok\", \"ok\": true, \"attempts\": 2}；scripts/runner.py:877 的控制流为 `tries = (1 + retry_n) if ti == 0 else 1`。",
      "fix": "改成指路式写法：① 代码控制流 `scripts/runner.py:877` 的 `tries = (1 + retry_n) if ti == 0 else 1`；② 落盘产物 `runs/_harness/retrythenok/RUN.json` 的 `nodes.w_try.attempts` = `2`、`nodes.w_try.status` = `ok`（该文件第 24-30 行）。二者有其一即可核。"
    },
    {
      "check": "verifiable",
      "severity": "blocking",
      "what": "A2「第 1 次的痕迹没被洗掉」只有自述，且「两个都能查到」没说是哪个文件、哪个字段 —— 验收官无从定位。",
      "evidence": "tasks/DAG-2026-11-06/fixtures/selfcheck-verifiable/DELIVERY-hit.md:9 原文：「我在终端里看过了，第 1 次的超报记录还在，第 2 次是成功的。两个都能查到。」 —— 「看过了」「能查到」都是自述，未给出路径与字段名。可引用的硬依据：runs/_harness/retrythenok/NODES/w_try.json 的 `attempts[0].checks.overclaim`（第 88-90 行为 [\"tasks/DAG-2026-11-06/fixtures/sandbox/notes.md\"]）、`attempts[0].ok` 为 false（第 85 行）、`attempts[1].ok` 为 true（第 109 行）。",
      "fix": "写明产物路径 + 字段名：`runs/_harness/retrythenok/NODES/w_try.json` 的 `attempts[0].checks.overclaim` 是 `[\"tasks/DAG-2026-11-06/fixtures/sandbox/notes.md\"]`、`attempts[0].ok` 是 `false`、`attempts[1].ok` 是 `true`。或者给 `scripts/runner.py` 里按 attempt 单独落 `<nid>.a<N>.json` 的那段控制流。"
    },
    {
      "check": "verifiable",
      "severity": "blocking",
      "what": "A3「越界不可洗」只有自述（并且用「放心」收尾），既无 file:line 也无产物字段名，验收官无法自行核对。",
      "evidence": "tasks/DAG-2026-11-06/fixtures/selfcheck-verifiable/DELIVERY-hit.md:13 原文：「这个我专门验证过：一旦越界就不再重试，节点直接判 fail。行为是对的，放心。」 —— 「专门验证过」「行为是对的」「放心」全是自述。可引用的硬依据：scripts/runner.py 的 `harm |= set(checks[\"out_of_scope\"]) | set(checks[\"readonly_violation\"])` 与 `elif harm: stop = \"harm\"`；产物 runs/_harness/retry-oos/NODES/w_bad.json 的 `attempt_count` 为 `1`、`harm`、`status` 为 `fail`。",
      "fix": "补两条硬依据之一：① 代码 `scripts/runner.py` 的 `harm |= ...` 与 `elif harm: stop = \"harm\"`；② 产物 `runs/_harness/retry-oos/NODES/w_bad.json` 的 `attempt_count` 是 `1`（不是 2）、`harm` 含 `tasks/DAG-2026-11-06/fixtures/sandbox/OFF-LIMITS.md`、`status` 是 `fail`。"
    }
  ]
}
```

> 抄录时对原文做了两处**无关语义**的整理：`evidence` 里指向 `scripts/runner.py` 的具体行号
> （原返回里带行号，会随代码漂移）改成了「该文件中的 `harm |= ...` 那两行」的定位式说法；其余逐字。

## 三、两次调用用的判据文本

### A. 旧判据（第 1、2 格用；`scripts/selfcheck.workflow.mjs` 里已被覆盖，这里是唯一的原文 —— **重跑时把它粘回去**）

```text
验收官只有 Read/Grep/Glob，没有 shell。检查每条验收标准能不能靠这三个工具核对；依赖「跑脚本才知道结果」的条目，是否至少给了源码控制流层面的依据。只报「完全无法核对」的条目。
```

### B. 新判据（第 3、4 格用，也是当前 `scripts/selfcheck.workflow.mjs` 里的版本）

```text
验收官只有 Read/Grep/Glob，没有 shell。逐条看交付说明里的**证据写法**怎么写的，判据是这一条：结论只靠「我跑过了 / 实测没问题 / 我看过了 / 验证过 / 放心」这类自述支撑，既没有 file:line，也没有可 Read 的产物路径 + 字段名，也没有命令输出原文的，一律报。哪怕结论事实上是对的 —— 你核不了就是你核不了，这条检查的就是「你能不能核」。依赖「跑脚本才知道结果」的验收标准，必须至少给出源码控制流（file:line）或落盘产物（可 Read 的路径 + 具体字段名）之一。反过来：只要有一处硬依据，就不要报。
```

## 四、这四格仍没能证明的

- **只用了一份夹具对**（一份 clean / 一份 hit）。换一份「自述稍多但仍给了半截依据」的材料会不会响，没测过。
- **判据靠词表**（「我跑过了 / 看过了 / 放心 / 经核查」）。换个说法可能绕过 —— 与 `CHECK_REUSES_WRITER_CMD` 同类弱点。
- **每格只跑了一次或两次**，`verifiable` 是 LLM 判定，换一次采样结果理论上可能不同
  （第 2 格跑了**两次**，都是 0 —— 这是目前唯一有重复观察的一格）。
