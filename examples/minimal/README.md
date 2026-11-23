# 最小可跑示例

这个目录演示**一次真实送审的最短路径**：一份可判定的任务书 → 一次复核 → 一份三态裁决。

| 文件 | 是什么 |
|---|---|
| `brief.md` | 任务书样例（按 `templates/REVIEW-BRIEF.md` 填好的形态） |
| `expected-verdict.json` | `ai_team_review` 的**返回值形状**示例 |

**这个例子刻意不是「一切通过」的样子。** 它演示的是这套东西唯一真正的价值：
**工人说「有测试、跑绿了」，但没有给出任何能核的东西 → 那条被判 `rework`。**
`pass` 遍地都是的示例教不会你任何东西。

---

## 一、先看懂 `expected-verdict.json` 在演示什么

它是 `ai_team_review` 的返回（字段名由 [`docs/CONTRACT.md`](../../docs/CONTRACT.md) §3.2 规定），里面**同时**发生了三件事：

| 现象 | 规则 |
|---|---|
| `findings[1].evidence` 是空串，`type` 被标成 `"E0"`，并且 `blocking` 里多出一条 `B1 … 证据缺失` | 解析器的硬规则：**每条 evidence 为空 → 标 `E0` 并进 blocking** |
| `findings[0]` / `findings[2]` 都是 `E2`，但 C2 声明要 `E3` | 强度门槛：`executable` 类**必须 `E3`**；不到门槛按 `fail` 判（不是 `unverified` —— 缺的不是环境，是证据） |
| `verdict` 是 `rework` 而不是 `pass` | 自相矛盾必然降级：**`verdict==='pass'` 且 `blocking` 非空 → 强制改 `rework`** |

还有几条没在这份示例里体现、但同属解析器硬规则（见 `docs/ARCHITECTURE.md` §2.5）：

- 没有 `verdict` 字段 / 解析失败 → **`escalate`**（不是 `pass`，也不是 `fail`）
- 原文里出现 "pass" 三个字母 **不算裁决** —— 只认结构化字段

> ⚠️ **形状是承诺，内容是示例。** 字段名与取值域是契约；具体哪条 pass、哪条 blocking，
> 取决于你的工作区与你的模型。**不要期待逐字相同。**

---

## 二、自己跑一遍（3 分钟，先不花钱）

### 步骤 1：造一个极小工作区

```powershell
$ws = Join-Path $env:TEMP ("slug-demo-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path (Join-Path $ws "src") | Out-Null
Set-Location $ws
```

`src/slug.js`（写文件请用编辑器的 UTF-8，别用会按本地代码页转码的重定向）：

```js
export function slugify(input) {
  return String(input ?? '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-');
}
```

`README.md`（故意**只写一个示例**，不写测试怎么跑 —— 这就是本轮会被打回的原因）：

````markdown
# slug demo

```js
slugify('Hello World') // => "hello-world"
```
````

把 `examples/minimal/brief.md` 复制成 `$ws/REVIEW-BRIEF.md`。

### 步骤 2：只组装 prompt，不花钱

让模型调（或你自己按工具签名发一次）：

```text
ai_team_review({ brief: "REVIEW-BRIEF.md", dryRun: true })
```

`dryRun` 只组装 prompt、**不调后端** —— 用来看清「验收官到底收到了什么」。
这是排查「为什么它没看见那个文件」的最快手段。

> **dry-run 永远不会返回 `pass`。** 没后端可调就没有裁决：它要么落到 `escalate`，要么明确报错。
> 这条写在契约里（[`docs/CONTRACT.md`](../../docs/CONTRACT.md) §7 第 9 条），因为它是最容易被
> 「顺手补个假裁决」破坏的地方 —— 一个会给假 `pass` 的工具，比没有工具更坏。

### 步骤 3：真跑一轮

```text
ai_team_review({ brief: "REVIEW-BRIEF.md", round: "1" })
```

需要后端可用（`codex` CLI 或 OpenAI 兼容 API，见 README §2.3），**会消耗额度**。

### 步骤 4：对照三件事

| 你要确认的 | 怎么确认 |
|---|---|
| 返回字段齐全且形状对 | 对照 `expected-verdict.json` 的**键名**（`verdict` / `confidence` / `blocking` / `findings` / `outDir` / `raw`） |
| C2 那条被判成 `fail` 或 `unverified`，**不是 `pass`** | 读 `findings` 里 `id="C2"` 那条 —— **这是本示例的核心断言** |
| `rework_brief` 是**可执行的最小指令** | 它要写着「改哪里、改成什么、怎么验」，不能是「优化一下测试」 |

### 步骤 5（关键）：自己造一个「假 pass」看它降不降级

手工把一份验收官输出改成不合规的样子，再走一遍解析：

- 删掉 `verdict` 字段 → 期望 **`escalate`**
- 保留 `verdict: "pass"` 但 `blocking` 非空 → 期望 **`rework`**
- 某条 `evidence: ""` → 期望该条 **`E0`** 且 `blocking` 多一条「证据缺失」

这一步是本仓库的 `node --check` + 断言机（`src/lib/verdict.test.mjs`）在做的事。
**你能自己复现它，比相信文档写的「有断言机」有力得多。**

---

## 三、这个示例故意没做什么

- **没有 happy path。** 想看 `pass` 长什么样，把上面 `README.md` 补上测试并跑通，
  再补一条「把断言改错、确认变红」的反证 —— 那时候 C2 才够 `E3`。
- **没有覆盖 DAG。** `ai_team_plan` / `ai_team_gate` / `ai_team_run` 的对象是一张图，
  不是一份任务书；最小图示例不在这份文件里（见 `docs/ARCHITECTURE.md` §3）。
- **没有真跑记录的截图。** 这个目录只承诺「形状」，不承诺「跑过」——
  跑过的东西长什么样，看 `docs/decisions/` 里那 116 份真实裁决。
