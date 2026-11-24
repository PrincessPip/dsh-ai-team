# dsh-ai-team

> A reviewer that **won't rubber-stamp your work**, for DeepSeek Harness: separation of powers, three-state verdicts, and no evidence means no pass.

**[中文](README.md)** | English · [Roadmap](ROADMAP.md)

---

## 0. In one sentence

The official DSH agent-team answers "**who does the work**". This project answers "**was the work actually done right**".

| | Official `dsh-experimental-agent-team` | This project `dsh-ai-team` |
|---|---|---|
| Problem it solves | Division of labour: who works on what, how messages are routed | **Verification and orchestration**: is the result correct, what runs next |
| What it provides | Teammate roster, shared task board, direct messages, `writeScopes` overlap warnings | Independent reviewer, three-state verdicts, evidence-strength thresholds, DAG planning with in-wave parallelism, gate chain |
| Member relationship | Flat roster; coordination only, no confinement | Triangle: **worker implements → reviewer re-checks → a human is involved only when the verdict is unclear** |

**The two are complementary and can be installed together**: the official one handles coordination, this one handles the criteria.

This project **does not re-implement** teammates / task boards / direct messages (the official packages already do), **does not bind** to any model vendor (backends are pluggable), and **does not embed** any private environment (proxies, VPS, subscriptions).

---

## 1. Why this exists

**Because an LLM saying "I'm done" cannot be trusted.**

This is not distrust of one particular model; it is a structural problem:

- **It can read its own glowing summary and then stamp it approved.** Put the worker's self-report ahead of the evidence the reviewer gathers itself, and the reviewer degrades into a paraphraser.
- **"I ran it once" and "I verified it" look identical.** "The endpoint returned 200" is a single observation; real failures hide in the second observation axis (process up, port up, logs fine — and the business logic still wrong).
- **A conclusion without evidence is always cheap to produce.** The moment "should be fine" and "theoretically correct" are acceptable phrasing, rework becomes a word game.

So this project does three things, one per problem above:

1. **Separation of powers** — worker (implements + self-tests + writes the delivery package) · reviewer (read-only re-check, the only party that issues verdicts) · arbiter (a human, appearing only on `escalate`). **Nobody signs their own certificate.**
   ⚠️ "Read-only" here means **discipline plus after-the-fact checkability, not sandbox isolation** — see the note at the end of §3.
2. **Three-state verdicts** — only `pass` / `rework` / `escalate`. There is no "basically fine". The second state comes back with a minimal list of required changes; the third packages everything into one decision a human must make.
3. **No evidence, no pass** — every `pass` must cite `file:line` or command output. Evidence is graded `E0`–`E3`; a `pass` that cannot cite evidence is mechanically downgraded to `rework`. These rules are enforced by gates and parsers, **not by the reviewer's good intentions**.

> In one line: replace "I finished the job and I say it's good" with "someone independent re-checked it, and the re-check left evidence anyone else can replay".

---

## 2. Five-minute setup

### 2.1 Prerequisites

| Need | Notes |
|---|---|
| DSH | Installed and `dsh --version` works (this repo targets the `0.2.x` plugin paradigm) |
| Node.js | ≥ 18 (the plugin itself is ESM) |
| Python 3 | ≥ 3.9 (`ai_team_plan` / `ai_team_gate` / `ai_team_run` drive the engine under `engine/`) |
| Review backend (either one) | **codex CLI** (`codex exec`, can read and write the workspace) or **any OpenAI-compatible API** (text only, no file access) |
| Dependency `@deepseek-ai/dsh-tools` | peer range `^0.2.0-rc.2`; npm's `latest` tag is `0.0.1-rc.1` (**does not satisfy it**) — pick the version explicitly, see §2.2 |

### 2.2 Install

#### First, resolve the dependency (you cannot skip this)

The plugin depends on two packages with **different sources**:

| Package | Requirement | How to get it |
|---|---|---|
| `@deepseek-ai/schemastery` | normal dependency `^3.18.1` | On npm (`latest` = `3.19.0`); install normally |
| `@deepseek-ai/dsh-tools` | **peer** `^0.2.0-rc.2` | ⚠️ see below |

The trap with `@deepseek-ai/dsh-tools` is not availability but the **dist-tag**:
its `latest` on npm is **`0.0.1-rc.1`**, which **does not satisfy** `^0.2.0-rc.2` —
so a plain `npm install @deepseek-ai/dsh-tools` gets the old build and the plugin will not run.
(A satisfying version does exist on npm, under the `next` tag: `0.2.2-rc.5`.)

Three routes, pick one:

```powershell
# 1) Ask for a version inside the range (official entry point; profile managed by pnpm)
dsh plugin --profile web add "@deepseek-ai/dsh-tools@next"     # or pin a specific 0.2.x

# 2) Reuse the copy that ships with your DSH install (0.2.1-rc.2) and link it into the profile
New-Item -ItemType Junction `
  -Path "$env:USERPROFILE\.dsh\profiles\web\node_modules\@deepseek-ai\dsh-tools" `
  -Target "<npm global prefix>\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-tools"

# 3) Install this plugin inside DSH's own tree (Node walks upwards, hitting the sibling dsh-tools)
cd "<npm global prefix>\node_modules\@deepseek-ai\dsh"
npm install /path/to/dsh-ai-team
```

> **How to tell it worked** (these two checks are the real criteria, more reliable than copying commands):
>
> ```powershell
> cd "$env:USERPROFILE\.dsh\profiles\web"      # your profile
> node -e "console.log(require.resolve('@deepseek-ai/dsh-tools'))"
> node -e "console.log(require('@deepseek-ai/dsh-tools/package.json').version)"   # must satisfy ^0.2.0-rc.2
> ```
>
> ⚠️ **This section documents the mechanism and the criteria, not a copy-paste install script.**
> The version numbers (`latest` = `0.0.1-rc.1` / `next` = `0.2.2-rc.5` / DSH-bundled `0.2.1-rc.2`) and the two paths were measured;
> which route to take depends on who manages your profile. **Trust the output of the two commands above.**
> If it will not install, paste the output of those two commands — far more useful than describing the symptom.

#### Then install the plugin

```powershell
# Route A: install from a GitHub tag (the public install path — always pin the tag; no bare package name, no default branch)
dsh plugin --profile web add github:PrincessPip/dsh-ai-team#v0.2.1

# Route B: from this repo directory (use this when you want to modify the source)
dsh plugin --profile web add /path/to/dsh-ai-team
```

> Public releases are cut as **git tags + GitHub Releases**: each version has a matching tag (e.g. `v0.2.1`) and is **not published to npm**.
> Two reasons we do not use npm: (1) the `dsh-ai-team` package name is already taken by a similar project; (2) the current egress to `npmjs.com` is blocked by DataDome.
> So install via the `github:` spec above and **pin the tag explicitly** (e.g. `#v0.2.1`); without a ref you get the latest commit on the default branch.
> `--profile` is mandatory; replace `web` with the profile you mount it into.
> `dsh plugin add` also resolves dependencies — if it goes to the registry for `dsh-tools` and complains about the range,
> put the dependency in place with one of the routes above first, then re-run this command.
> Installation **does not take effect immediately**: the tool table is assembled when a session starts, so **open a new session**
> before expecting the `ai_team_*` tools.

### 2.3 Minimal configuration

Plugin configuration lives in the profile patch. Merge the `insert` block from the repo-root `cordis.patch.yml`
into `<DSH_HOME>/profiles/<profile>/cordis.patch.yml` (keep the `id` unique):

```yaml
- insert:
    - id: dsh-ai-team
      name: dsh-ai-team
      config:
        backend: codex                          # reviewer backend: codex | openai
        model: ""                               # empty = backend default model
        codexPath: codex                        # codex CLI executable
        openaiBaseUrl: https://api.openai.com/v1
        openaiApiKeyEnv: OPENAI_API_KEY         # <- the ENV VAR NAME only, never the key itself
        timeoutMs: 3600000
        engineDir: ""                           # empty = the bundled engine/
        pythonPath: python
        nodePath: node
        outDirName: .ai-team
        swarmMode: false                        # 0.2.0+: swarm mode, see §3.5
```

**Security red line**: `openaiApiKeyEnv` accepts **an environment-variable name only**, never a key literal; keys never appear in logs or return values.
Keep the key in an environment variable or your credential manager, **never in a config file**.

For the remaining fields and their defaults see [`docs/CONTRACT.md`](docs/CONTRACT.md) §4.

### 2.4 First call

In a new session, have the model make these calls (or just describe the order to it):

```text
1) ai_team_init()                                    # lay the team templates into the current workspace
2) ai_team_review({ brief: "REVIEW-BRIEF.md", round: "1" })
3) ai_team_gate({ graph: ".ai-team/runs/<runId>/GRAPH.json" })   # only when you have a DAG
```

`ai_team_init` lays down (existing files are skipped, never overwritten):

```text
<target>/AI-TEAM.md          <- team conventions snippet (paste into or reference from your AGENTS.md)
<target>/REVIEW-BRIEF.md     <- brief template
<target>/INC-TEMPLATE.md     <- incident template
<target>/.ai-team/roles/     <- three role prompts: worker / reviewer / fire-chief
```

`ai_team_review` returns a **structured verdict** with a fixed shape (non-conforming replies are downgraded by `verdict.js` and never default to `pass`):

```json
{
  "verdict": "rework",
  "confidence": 0.74,
  "blocking": ["B1: acceptance criterion C3 claims 'covered' but cites no file:line — evidence missing"],
  "findings": [
    { "id": "C1", "claim": "the new function has unit tests", "evidence": "src/x.test.mjs:12 \"it('...')\"", "type": "E2" }
  ],
  "outDir": "/path/to/workspace/.ai-team/reviews/round1",
  "raw": "... reviewer's raw text (truncated to 20000 chars) ..."
}
```

A minimal runnable sample (with the expected verdict shape) is in [`examples/minimal/`](examples/minimal/).

---

## 3. The five tools at a glance

| Tool | In one line | Parameters (`*` = required) | Returns |
|---|---|---|---|
| **`ai_team_init`** | Lay the team templates into the current workspace | `target` (default cwd) · `force` (default false) | `{ created, skipped, root }` |
| **`ai_team_review`** | Send to the independent reviewer, get a three-state verdict (the core) | `brief*` (text or `.md` path) · `round` (default `"1"`) · `backend` · `model` · `dryRun` | `{ verdict, confidence, blocking, findings, outDir, raw }` |
| **`ai_team_plan`** | Requirement → DAG graph (calls `engine/manager.py`) | `brief*` · `outDir` · `model` · `dryRun` | `{ graphPath, runId, nodeCount, summary }` |
| **`ai_team_gate`** | Judge a graph; bad graphs must not reach the runner (calls `engine/dag-gate.mjs`) | `graph*` (`GRAPH.json` path) | `{ ok, errs, warns, codes }` |
| **`ai_team_run`** | Execute a graph wave by wave (calls `engine/runner.py`) | `graph*` · `engine` (`headless`/`fixture`/`codex`, default `fixture`) · `runId` · `fixtureDir` (`fixture` only: scenario dir containing `spec.json`) | `{ runId, result, nodes, runDir }` |

Three usage rules (also stated in each tool description):

1. **When `ai_team_gate` returns `ok=false`, stop.** No "let's just run it" — a graph the gate rejected only produces evidence you cannot attribute.
2. **`ai_team_run` defaults to the `fixture` engine**: fake worker, seconds, free, does not touch the main tree. **It verifies the process, not the product.** Pass `engine: "headless"` to really call a model.
3. **`ai_team_review` returning `rework` is not an exception.** It is a normal result; work from `blocking` and `findings`. Only an unavailable backend throws.

If `fixtureDir` is omitted it falls back to `<cwd>/.ai-team/fixtures`, then to the bundled `scenarios/honest`;
**passing it with a non-`fixture` engine is an explicit error** (never silently swallowed).
Details and the list of 40 bundled scenarios: [`docs/CONTRACT.md`](docs/CONTRACT.md) §3.5.

### 3.5 Swarm mode (new in 0.2.0)

A two-state switch in the composer turns the Lead from **worker** into **dispatcher**. Off by default; it changes no tool permission.

```text
swarm OFF (default)                        swarm ON
────────────────────                       ────────────────────
Lead writes the files itself               Lead must plan first:
                                           ai_team_plan -> ai_team_gate -> ai_team_run
                                           read-only work stays unrestricted
                                           deviation must be declared: [swarm deviation: reason]
```

- **Where**: the `蜂群 ON` / `蜂群 OFF` button sits right after the permission control in the composer.
  Off state is `transparent` + `--dsw-alias-border-l4` outline + secondary label colour (a normal white-on-light look in the light theme,
  deep-on-dark in the dark theme, never a white patch); on state is the brand blue `#5686fe` with white text.
- **How it works**: one boolean config field (`swarmMode`, a *volatile* Schemastery field) plus a `systemPrompt.section`
  whose `text()` is re-read on **every model step**. Toggling writes through the client's `configForms` into the profile patch
  and the loader commits the new value into the live config — **no `dsh web` restart, no new session**; the very next model step
  carries (or drops) the injected block. Only the browser half needs a page refresh to pick up new client code.
- **Why not restrict tools**: the tool table is assembled when the session starts, so a mid-session toggle could never take effect.
  Prompt assembly, by contrast, happens per model step — which is exactly the "turn it on between two turns and the Lead is reminded
  immediately" behaviour the feature is for.
- **The escape hatch is the point**: a rule the model can silently ignore is worse than none. Rule 4 requires an explicit
  `[swarm deviation: reason]` at the start of the reply, so a violation becomes a **visible, auditable declaration** that
  can be machine-checked afterwards.

Design notes, verification evidence and rollback: [`tasks/SWARM-MODE-2026-11-24/DELIVERY.md`](tasks/SWARM-MODE-2026-11-24/DELIVERY.md).

### What the reviewer's "read-only" actually means (do not misread it)

**It is discipline plus after-the-fact checkability, not sandbox isolation.** All three statements are true; drop one and you will misjudge:

| Statement | Right or wrong |
|---|---|
| "The reviewer does not write to the workspace" | ✅ right — this is its **behavioural discipline**, written into the prompt and the contract |
| "Therefore it cannot perform writes" | ❌ **wrong** — there is no process-level confinement; technically it can |
| "Therefore out-of-scope writes would go unnoticed" | ❌ also wrong — before/after snapshots (or `git status`) reveal **afterwards** what changed |

**Why not simply use a `read-only` sandbox?** Because the measured cost is too high: on Windows,
`--sandbox read-only` effectively **forbids running any command**, even `Get-Content` is `blocked by policy` —
the reviewer cannot read files or run commands and is left judging the narrative alone, which means no real review happened.

So this plugin's stance is: **grant the capability to do work, detect out-of-scope writes after the fact, and label the
enforcement level honestly.** The codex backend's sandbox level is configurable in the contract (`codexSandbox`, default
`workspace-write`), and return values carry an enforcement level (e.g. `enforcement: 'snapshot_only'`) —
**"checkable afterwards" must never be sold as "isolated"**; they are different promises. See [`docs/CONTRACT.md`](docs/CONTRACT.md) §5.1.

---

## 4. Using it alongside the official agent-team

The two do not conflict. Division of labour:

```text
+- official agent-team: who does the work -------------------------+
|  Lead -- spawn_teammate --> worker A / worker B                  |
|         team_task_*  <--> shared task board (who is doing what)  |
|         send_message <--> direct messages                        |
+------------------------------------------------------------------+
                          |  one piece of work is finished
                          v
+- this project: was it done right -------------------------------+
|  ai_team_review --> independent reviewer (read-only, checkable  |
|                     afterwards) --> three-state verdict          |
|  ai_team_plan/gate/run --> DAG planning -> criteria -> run waves |
+------------------------------------------------------------------+
```

**Recommended wiring**: after a task on the board is delivered, have **someone other than the worker** call `ai_team_review`
(having the Lead do it is simplest). A worker calling `ai_team_review` on its own work removes one leg of the separation of powers —
the tools will not stop you, but the exercise is meaningless.

---

## 5. What's in the repo

| Path | What it is |
|---|---|
| `src/` | The plugin: five tools + three backend adapters + the verdict parser; `swarm.js` (swarm prompt and switch reader) and `client.js` (browser half: the composer switch) since 0.2.0 |
| `engine/` | The engine migrated **verbatim from production** (Python / Node): planner, gate, runner, gate chain, fire chief |
| `tasks/DAG-2026-11-06/fixtures/` | **Regression fixtures, bundled** (90 files / ~240 KB): **40 scenarios** (`scenarios/*/spec.json`, covering overclaiming / under-reporting / out-of-scope writes / tampering with evidence / retries / parallelism / conditional jumps ...) + 35 sample graphs + a fake worker. `ai_team_run --engine fixture` runs the whole flow at zero cost with this, and it doubles as a ready-made regression bench: same graph, different `fixtureDir` → "honest worker passes / dishonest worker fails". ⚠️ The path must not move (the engine resolves it by the original relative path) |
| `templates/` | Team templates: conventions snippet, brief, incident form, three role prompts |
| `docs/CONTRACT.md` | **The interface contract, single source of truth**: tool parameters and returns, Config, adapters, verdict rules, honest limits |
| `docs/` | [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`PORTING.md`](docs/PORTING.md) · redacted contract and policy texts · 116 real verdict JSON files |
| `examples/minimal/` | Minimal runnable sample: one brief plus the expected verdict shape |
| `tasks/SWARM-MODE-2026-11-24/` | Swarm-mode design, delivery record and machine-replayable evidence |

Prefer the criteria over the code? Read these three:

- [`docs/REVIEW-CONTRACT.md`](docs/REVIEW-CONTRACT.md) — the reviewer's field manual: three states, evidence levels `E0`–`E3`, observation classes `D1`–`D5`, anti-sycophancy rules
- [`docs/TRIGGER-POLICY.md`](docs/TRIGGER-POLICY.md) — when to invoke the reviewer (three tiers: exempt / keep / send for review), fire-chief tier, pre-review gates
- [`docs/decisions/`](docs/decisions/) — 116 real verdicts; you can see directly what "not enough evidence" looks like

---

## 6. Honest limitations

**This section does not flatter itself.** The whole point of the project is "no evidence, no pass"; a README full of boasting would refute itself.

1. **The engine (`engine/`) is "verbatim from production", not a cross-platform rewrite.**
   Every part bound to Windows + PowerShell + specific backends (codex CLI / a local gateway) is **left untouched**.
   The itemised list (down to `file:line`) and the change points are in [`docs/PORTING.md`](docs/PORTING.md).
   — Running things under `engine/` directly on Linux / macOS will fail. That is not "untested"; it was **never designed for it**.

2. **The `fixture` engine is a fake worker — but it is runnable, not a missing piece.**
   The fixtures are bundled as a whole directory (`tasks/DAG-2026-11-06/fixtures/`, 90 files / ~240 KB);
   one command runs the whole flow at **zero cost, no model calls, no writes to the main tree**:

   ```powershell
   python engine/runner.py tasks/DAG-2026-11-06/fixtures/graphs/g3.json `
     --engine fixture --fixture-dir tasks/DAG-2026-11-06/fixtures/scenarios/honest
   ```

   Expected: `gate OK` · 3 waves · all 3 nodes `ok` · `result PASS` · evidence package under `runs/<id>/`.
   It verifies the **process** (queueing, waves, retries, escalation, evidence layout), **not the product**, and calls no model —
   `headless` is what really calls a model. So a `result=pass` from `fixture` **must not be used as delivery evidence**:
   the `FAKE` marker inside the evidence package exists to stop exactly that misuse.

   ⚠️ **The fixture is not idempotent: it corrupts itself on the second run.** Scenarios modify files under
   `tasks/DAG-2026-11-06/fixtures/sandbox/` (`honest` rewrites `notes.md` from 3 entries to 4).
   On the second run the content written is identical to what is already on disk → **zero change** → judged `overclaim`,
   **with exactly the same failure string as the real overclaim scenario**. So: **restore that sandbox directory before running**,
   otherwise you are looking at a fake failure. Item-by-item explanation and the restore command: [`docs/PORTING.md`](docs/PORTING.md) §9.1.

   ⚠️ **Only one runner at a time.** `runs/` is included in the snapshot, so two concurrent runs see each other writing evidence packages
   and fail as `evidence_write` — that is not a bug, it is the "evidence must not be contaminated" criterion doing its job.
   The harm is not "it won't run", it is **a false conclusion** (you get a FAIL that looks real). If you hit it, rerun serially;
   do not disable the criterion. See [`docs/PORTING.md`](docs/PORTING.md) §9.2.

3. **`result=pass` only means the process completed; product correctness needs a separate `verify-product` run.**
   In the original production project each real run ships its own `verify-product.py` to judge the product — **not bundled here**.
   In other words: you can get a "process pass" here, but **the ruler for judging the product did not come along**; write your own.

4. **The reviewer can still be sycophantic (a general LLM weakness).**
   The countermeasures here are **contract constraints plus mandatory evidence**: exactly three verdicts, every `pass` must cite evidence,
   no citation means downgrade, evidence graded by strength, gates checking mechanically. This **raises the cost of faking** but
   **promises nothing like 100%**. It stops "said it was fine", not two carefully fabricated different observations —
   the real defence remains spot-checking old work and having a human look again.

Two further facts at the same level (not part of contract §8, but equally limits):

5. **The "five-minute setup" section follows the contract in [`docs/CONTRACT.md`](docs/CONTRACT.md).**
   If the version you installed disagrees with this document, **the contract wins** (and the tools' actual `description` wins over both).

6. **The `*.ps1` files under `engine/` need PowerShell, and they affect more entry points than you would guess.**
   No cross-platform replacements are provided. Point by point:

   | Entry point | Does it really need PowerShell? |
   |---|---|
   | `ai_team_gate` (pure Node) | **No** |
   | `ai_team_plan`, real run | **Yes** — it first runs an egress precheck (`manager.py:423-424`) |
   | `ai_team_plan` with `dryRun` | No precheck, but **still requires the codex CLI to be found** (`manager.py:411`) |
   | `ai_team_run` + `fixture` / `headless` | **No** (`runner.py:2004-2009` explicitly skips the precheck) |
   | `ai_team_run` + `codex` | **Yes** — the egress precheck hard-blocks (`runner.py:1998-2003`) |
   | Ledger / reconcile / index / backfill (those four `.ps1`) | **Yes**, with no alternative path |

   How to handle non-Windows hosts: [`docs/PORTING.md`](docs/PORTING.md) §1 and §4.

---

## 7. License

MIT, see [`LICENSE`](LICENSE).

The raw material under `engine/` and `docs/` comes from the same author's production project and has been redacted;
the scope of redaction and the reasons why some files are not published are in [`reports/REDACTION-REPORT.md`](reports/REDACTION-REPORT.md).

---

## 8. Roadmap

What is planned, what is deliberately not planned, and what is blocked on evidence: [**ROADMAP.md**](ROADMAP.md).
