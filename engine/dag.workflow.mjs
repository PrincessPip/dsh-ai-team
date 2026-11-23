// dag.workflow.mjs —— DSH `workflow` 工具的脚本体：通用 DAG 解释器（DESIGN §8 Step 0）
//
// 为什么（DESIGN §5.1）：写「解释器」，不写「编译器」。图是纯数据 `args.graph`，
//   脚本只写一次、测一次；图因此可校验 / 可 diff / 可 dry-run / 可复现。
//
// 这不是什么：它不判断任务该不该做，也不调模型判图合不合法。能不能跑由死规则判（Gate），
//   跑不动的地方显式停下并报出来，绝不把失败悄悄吞进汇总。
//
// 调用方式（从会话里传，不是手敲）：
//   workflow({ script: <本文件正文>, args: {...} })
//     args.graph        任务图对象（DESIGN §4）
//     args.graphJson    任务图的 JSON 字符串（有则覆盖 args.graph，方便把夹具文件原样塞进来）
//     args.dryRun       true → 只跑 Gate + 出调度波次/关键路径，**一个 agent 都不调**
//     args.graphs       [仅 dry-run] 一组图，逐张跑 Gate 并返回矩阵（做单变量对照用）
//
// 返回体：dryRun → { errs, warns, waves, criticalPath, ... }；真跑 → { nodes, counts, replan_needed, ... }
//
// 已知缺口（v1，别当全能）：
//   1. F5：workflow 里到不了订阅通道 → `check` / `strong` 两档只能落到会话自己的模型（现在是
//      deepseek-flash）。返回体的 `degraded_tiers` 会点名，**不是静默替换**；其中 `check` 的结果
//      **不能**当 REVIEW-CONTRACT 意义上的验收裁决用（只读硬锁在 workflow 里拿不到）。
//      ⚠️ 2026-11-23（DAG-MIXED-MODEL）：`strong` 档已加回并指向订阅 GPT，但**在这条路径上仍是降级**——
//      它要真生效必须走 `scripts/runner.py`（那边用 `--patch` overlay 换 provider/model）。
//      解释器保留 strong 是为了**与 runner 的档位表对账**（同一张图两边必须用同一个档位名）。
//   2. 没有美元计价：引擎不回传 cost，只数 agent 次数；`budget.max_usd` 只能提醒不能拦。
//   3. abort 是尽力而为：所有节点一次性起跑，已经在跑的节点不会被取消。
//   4. 关键路径按「节点数」算，不按耗时（v1 没有可靠的耗时估计）。
//   5. 脚本没有文件系统 / 网络 / 定时器，图必须从 args 进来。
//   6. 返回体里的 `patch` 截到 1200 字符（下游 integrate 节点拿到的是全文）。
//   7. `scope` 断言只在节点回了 `changed` 时才真核；没回就记 `scope.ok = null` 说没核。
//
// 成本：dry-run = 0 个 agent。真跑 = 每个节点 (1 + retry) 次 deepseek 调用，便宜。

// ---------------------------------------------------------------- 档位表（DESIGN §6）

const TIERS = {
  scout: { provider: 'deepseek-official', model: 'deepseek-flash', channel: 'official' },
  work: { provider: 'deepseek-official', model: 'deepseek-flash', channel: 'official' },
  integrate: { provider: 'deepseek-official', model: 'deepseek-flash', channel: 'official' },
  // ⚠️ 2026-11-23（`tasks/DAG-MIXED-MODEL-2026-11-23/`）：`strong` 档**以另一个含义**加回来。
  // 2026-11-12 移除时它指 deepseek-v4-pro（比 flash 更弱更贵、纯文本）；现在它指**订阅通道的
  // GPT-6.2-Astra**，经本地网关 `codex-gw` 转发到 ChatGPT 订阅。判断型 kind 走它，吞吐型留 flash。
  // 与 `runner.py:TIERS` 必须逐字一致（provider + model 两个值；runner 侧没有 `channel` 字段）。
  strong: {
    provider: 'codex-gw',
    model: 'gpt-6.2-astra',
    channel: 'subscription',
    why: '订阅通道（ChatGPT 订阅，经本地网关 codex-gw 转发）。本解释器（workflow 工具）**到不了**它 —— 见文件头 F5：workflow 内只能跑会话自己的模型，换模型要 runner 的 --patch overlay。所以本档在 workflow 里是**记录用**：Gate 靠它核档位名合法、dry-run 靠它算波次与关键路径，真跑请走 scripts/runner.py（那边 overlay 会真生效）。',
  },
  // §6 写的是「验收 claude（订阅，只读硬锁）」—— 同上够不到。
  check: {
    provider: 'deepseek-official',
    model: 'deepseek-flash',
    channel: 'official-substitute',
    why: '§6 的验收是 claude 订阅通道 + 只读硬锁，workflow 内到不了（F5）。v1 用 flash：§2.4 的 A/B 里 flash 在审查题上给得更全（14 条 vs 5 条）。此档结论**不是**契约意义的裁决。⚠️ 本档保留给「显式写 tier: check」的节点；`kind: check` 的**默认档**自 2026-11-23 起是 `strong`（见 KIND_TIER）。',
  },
};

// kind → 默认档（图里可显式覆盖 node.tier）
const KIND_TIER = {
  scout: 'scout',
  // 2026-11-23 伙伴拍板：判断型 kind 默认上 GPT（DESIGN §9.3 的定稿映射）
  contract: 'strong',
  work: 'work',
  integrate: 'strong',
  check: 'strong',
  replan: 'strong',
};

const KINDS = ['scout', 'contract', 'work', 'integrate', 'check', 'replan'];

const KIND_ROLE = {
  scout: '只读侦察员',
  contract: '接口/契约定义员',
  work: '实施员（只交 diff，不碰主树）',
  integrate: '集成员（本轮唯一写手）',
  check: '只读核查员',
  replan: '返工规划员',
};

// 节点结构化输出的字段库。schema 是按 node.out 现搭的：没声明的字段模型拿不到，
// 所以 `ctx.from` / `when.ref` 引用的字段必须在上游的 out 里声明过（Gate 会核）。
// `verdict` 刻意不设 enum：DESIGN §4 的条件示例用的是 needs_migration 这类自定义值，
// 锁死枚举会把合法图拒掉。代价是 `when` 对自由文本做 eq 天然脆 —— Gate 会打 WHEN_BRITTLE。
const FIELDS = {
  summary: { type: 'string' },
  missing: { type: 'array', items: { type: 'string' } },
  artifacts: { type: 'array', items: { type: 'string' } },
  changed: { type: 'array', items: { type: 'string' } },
  // v2（2026-11-07）：给 accept.allow_from 提供可引用的动态路径清单。
  // 为什么单开一个字段、不复用 artifacts：artifacts 在 PLUMBING 里（永远在 schema 里），
  // declares() 对它恒真 —— 拿它当 allowlist 等于任何字符串都能放行，而且它已经另有用途
  // （那张真图里 integrate.artifacts 装的是哈希基线）。
  allow_paths: { type: 'array', items: { type: 'string' }, description: '本节点产出的、下游可以额外改的路径清单（allow_from 只能引用数组类字段）' },
  facts: { type: 'string' },
  iface: { type: 'string' },
  patch: { type: 'string' },
  // 2026-11-09（F1）：`out` 声明了 `patch` 的节点必须交 diff、**不许碰主树**
  // （DESIGN.md:203「员工不碰主树，只返回 diff；唯一写手是集成节点」）。
  // 于是「这个 patch 会改到哪些文件」需要一个独立字段 —— 原先它被塞进 `changed`，
  // 而 `changed` 在执行器里是「**实际落盘**改了哪些」，两义合一的结果是：
  // 照设计做（只交 patch）的节点被判 overclaim，违规直接写主树的反而判 ok。
  patch_targets: { type: 'array', items: { type: 'string' }, description: '本节点的 patch 会改到的文件（相对工程根）。它不落盘，所以不进 changed' },
  verdict: { type: 'string', description: '判断类结论。本工程契约的约定词表是 pass / rework / escalate' },
  notes: { type: 'string' },
};

// 数组类字段：只有这些能进 accept.allow_from（自由文本当路径清单永远匹配不上，
// 而「匹配不上」在 scopeCheck 里表现为越界 fail —— 是个难查的假失败）。
const ARRAY_FIELDS = ['missing', 'artifacts', 'changed', 'allow_paths', 'patch_targets'];

// 自由文本字段：拿去 when.eq / when.in 比字符串不是确定性的（模型换个说法就断）
const FREE_TEXT = ['summary', 'facts', 'iface', 'patch', 'verdict', 'notes'];

// 永远在 schema 里的管道字段
const PLUMBING = ['summary', 'missing', 'artifacts'];

// 这些 code 是「脚本自己写错了」，不是「节点失败了」：直接抛，别装作节点挂了。
// 依据 dsh-workflow-ptc/lib/index.js（源码直读）：agent() 的 startAgent 抛错包成 AGENT_START，
// 选项/ schema / 参数不合规分别是 UNSUPPORTED_OPTION / UNSUPPORTED_SCHEMA / INVALID_ARGUMENT，
// 总数超限是 AGENT_CAP；WorkflowError 默认 fatal，经 parallel/pipeline 会整脚本终止。
const FATAL = ['UNSUPPORTED_OPTION', 'UNSUPPORTED_SCHEMA', 'INVALID_ARGUMENT', 'AGENT_CAP', 'ITEM_CAP'];
// AGENT_RESULT（子 agent 跑挂）与 AGENT_START 刻意**不在**上面：按 F4 要就地接住，不能让它们炸图。

const ACCEPT_TYPES = ['cmd', 'assert', 'scope', 'review'];

// 2026-11-10（EVIDENCE-GUARD C1 / F6）：**快照排除区**。逐字对齐 runner 的 `EXCLUDE_DIRS`
// （`scripts/runner.py:98`「EXCLUDE_DIRS = {"node_modules", ".git", "runs", "__pycache__", ".pytest_cache", ".venv"}」）
// —— 改一处必须两处一起改，否则「Gate 允许的写」与「runner 看得见的写」又会错位。
// 为什么要在 Gate 拦：这些目录不进全量快照，于是「声明要写它」= 声明一个**判据看不见的写**，
// 既不会判 `underclaim` 也不会判 `overclaim`（E2E 里 `integ.writes` 就含 `runs/plan-step1-demo/…`）。
const EXCLUDED_TOP = ['runs', 'node_modules', '.git', '__pycache__', '.pytest_cache', '.venv'];

// ---------------------------------------------------------------- 小工具

const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);

// 这条路径落在快照排除区的哪一格（不是就返回 null）。
//
// ⚠️ **判据是「任意一段命中」，不是「第一段命中」**（2026-11-10 第 1 轮验收官 B1 抓的）：
// runner 那边是 `runner.py:525`「if any(seg in EXCLUDE_DIRS for seg in segs):」—— 只要路径里
// 任何一段是排除目录，快照就跳过它。第一版写成只看第一段，于是
// `lib/runs/out.json` / `src/__pycache__/m.pyc` / `tools/node_modules/a.js` 全部 Gate OK，
// 而 runner 对它们**完全不可见**（连轻量指纹都没有）—— F6 原封不动地留着。
// 归一规则与 runner 的 `norm_path` 同口径：只做分隔符与 `./` 前缀，**不做**大小写折叠或 `a/../b` 归约。
const excludedTopOf = (p) => {
  const s = String(p).replace(/\\/g, '/').replace(/^\.\//, '').replace(/\/+$/, '');
  const hit = s.split('/').filter((x) => x && EXCLUDED_TOP.indexOf(x) >= 0);
  return hit.length ? hit[0] : null;
};

// 只用自有属性查表：不然 tier/字段名写成 "constructor"、"toString" 会命中原型链
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

// id 会当对象键用（byId / memo / results / 祖先表…），撞上 Object 原型键会静默错判
const RESERVED_IDS = ['__proto__', 'prototype', 'constructor', 'tostring', 'valueof', 'hasownproperty', 'isprototypeof', 'propertyisenumerable', 'tolocalestring'];

// 路径比较用：反斜杠→斜杠、折叠 `.` 与 `..`、去 ./、去尾斜杠、小写（Windows 下同名不同大小写会漏判）。
// v2.1（第 1 轮验收 N5）：过去不折叠 `..`，`tests/../scripts/manager.py` 能通过 allow_under 前缀检查。
const norm = (p) => {
  const parts = [];
  for (const seg of String(p).replace(/\\/g, '/').split('/')) {
    if (!seg || seg === '.') continue;
    if (seg === '..') {
      parts.pop();
      continue;
    }
    parts.push(seg);
  }
  return parts.join('/').toLowerCase();
};

// 命令归一：判「check 节点与写手是不是同一条命令」用。只折叠空白与大小写，不动参数 ——
// 动参数就成了「猜等价」，静态 Gate 猜不准。
const cmdKey = (s) => String(s).trim().toLowerCase().replace(/\s+/g, ' ');

const str = (x) => (x === undefined || x === null ? '' : String(x));
const clip = (x, n) => {
  const s = typeof x === 'string' ? x : JSON.stringify(x);
  return s.length > n ? s.slice(0, n) + ' …(截断 ' + (s.length - n) + ' 字)' : s;
};
const j = (x) => JSON.stringify(x === undefined ? null : x);

// ---------------------------------------------------------------- Gate：validate(G)

// 死规则，不调模型。返回 {errs, warns, byId, ids}。
// errs 非空 = 整张图拒收（真跑模式下连一个 agent 都不会起）。
function validate(G) {
  const errs = [];
  const warns = [];
  const err = (code, msg) => errs.push({ code: code, msg: msg });
  const warn = (code, msg) => warns.push({ code: code, msg: msg });

  if (!isObj(G)) {
    err('GRAPH_SHAPE', '图必须是 JSON 对象');
    return { errs: errs, warns: warns, byId: {}, ids: [] };
  }
  if (G.v !== 1) err('GRAPH_VERSION', 'v 必须是 1，收到 ' + j(G.v));
  if (typeof G.plan_id !== 'string' || !G.plan_id) warn('GRAPH_PLAN_ID', '缺 plan_id：事后没法点名追溯是哪一版图（§3 ③ 人点头要对着同一份图）');

  const nodes = G.nodes;
  if (!Array.isArray(nodes) || nodes.length === 0) {
    err('GRAPH_NODES', 'nodes 必须是非空数组');
    return { errs: errs, warns: warns, byId: {}, ids: [] };
  }

  if (G.hot_files !== undefined && !Array.isArray(G.hot_files)) err('GRAPH_HOT', 'hot_files 必须是数组');
  const hot = [];
  if (Array.isArray(G.hot_files)) for (const h of G.hot_files) hot.push({ key: norm(h), raw: str(h) });

  const byId = {};
  const ids = [];

  // ---- 第一遍：单节点内部 ----
  for (const n of nodes) {
    if (!isObj(n) || typeof n.id !== 'string' || n.id === '') {
      err('NODE_SHAPE', '有节点缺 id（每个节点必须是带非空字符串 id 的对象）');
      continue;
    }
    if (RESERVED_IDS.indexOf(n.id.toLowerCase()) >= 0) {
      err('NODE_ID_RESERVED', 'id 不能用 ' + j(n.id) + '：它会当对象键用，撞上 JS 原型链会静默错判');
      continue;
    }
    if (own(byId, n.id)) {
      err('NODE_DUP_ID', 'id 重复：' + n.id);
      continue;
    }
    byId[n.id] = n;
    ids.push(n.id);
    const at = n.id + '：';

    if (KINDS.indexOf(n.kind) < 0) err('NODE_KIND', at + 'kind 不合法（' + j(n.kind) + '），只能是 ' + KINDS.join('/'));
    if (typeof n.do !== 'string' || !n.do) err('NODE_DO', at + '缺 do（一句话说清做什么）');
    if (n.after !== undefined && !Array.isArray(n.after)) err('NODE_AFTER', at + 'after 必须是 id 数组');
    if (n.join !== undefined && n.join !== 'all' && n.join !== 'any') err('NODE_JOIN', at + 'join 只能是 all / any（默认 all）');
    if (n.tier !== undefined && !own(TIERS, n.tier)) err('NODE_TIER', at + 'tier 未定义：' + j(n.tier) + '（可用 ' + Object.keys(TIERS).join('/') + '）');
    if (n.critical !== undefined && typeof n.critical !== 'boolean') warn('NODE_CRITICAL', at + 'critical 是布尔量，现在写成 ' + j(n.critical) + '（v1 不影响调度，只是标注）');

    if (n.writes !== undefined) {
      if (!Array.isArray(n.writes) || n.writes.some((w) => typeof w !== 'string' || !w)) err('NODE_WRITES', at + 'writes 必须是非空字符串数组');
    }
    // 2026-11-10（EVIDENCE-GUARD C1）：写集不许指向快照排除区（F6）。
    // 这一条是**拒收**不是提醒：`writes` 是 allowlist 的一半真值来源，它指向一个判据看不见的目录时，
    // 那句 scope 断言就成了空话 —— 而空话比没有断言更糟（看起来核过了）。
    if (Array.isArray(n.writes)) {
      for (const w of n.writes) {
        const hit = excludedTopOf(w);
        if (hit)
          err(
            'SCOPE_EXCLUDED_PATH',
            at + 'writes 里的 ' + j(w) + ' 在快照排除区（' + hit + '/）内：runner 的快照对这个目录' +
              '**不算内容哈希、也常常整目录跳过**，所以这个写既不会判 underclaim 也不会判 overclaim（F6）。' +
              '要么把目标改到快照内，要么从 writes 里删掉',
          );
      }
    }
    if (n.requests !== undefined) {
      if (!Array.isArray(n.requests) || n.requests.some((r) => typeof r !== 'string' || !r)) err('NODE_REQUESTS', at + 'requests 必须是字符串数组');
    }
    if (n.out !== undefined) {
      if (!Array.isArray(n.out)) err('NODE_OUT', at + 'out 必须是数组');
      else
        for (const f of n.out) {
          if (f === 'status') continue;
          if (!own(FIELDS, f)) err('NODE_OUT', at + 'out 里有未知字段 ' + j(f) + '（可用 ' + Object.keys(FIELDS).join('/') + '）');
        }
      // 2026-11-09（E2EFIX 第 1 轮 B1 / N5）：`out` 声明 patch 的两条硬要求。
      // ① 只读 kind 与 integrate 都不许声明 patch：前者交不出真 diff，且并发路径拿不到
      //    attempt 级落盘窗口、判不了 fs_write_forbidden（B3）；后者本来就是落盘的写手，
      //    声明了 patch 就永远满足不了自己（任何落盘都触发 fs_write_forbidden）。
      // ② 必须同时声明 patch_targets：没有它 patch_out_of_scope 就没有输入，
      //    等于一条**永远不响**的判据（本轮真图 w_script / w_docs 就栽在这）。
      const outs = Array.isArray(n.out) ? n.out.map(String) : [];
      if (outs.indexOf('patch') >= 0) {
        if (n.kind !== 'work')
          err('PATCH_REQUIRES_WORK', at + 'out 里有 patch，但 kind=' + j(n.kind) + '：patch 只能由 kind=work 交（只读节点交不出 diff，integrate 本来就要落盘）');
        if (outs.indexOf('patch_targets') < 0)
          err('PATCH_WITHOUT_TARGETS', at + 'out 里有 patch 但没有 patch_targets：「这个 patch 会改到哪」没人说得清，patch_out_of_scope 就成了空判据。加上 patch_targets');
      }
      // N6：patch 节点的 `accept.cmd` 在主树上跑，看不见自己的 patch —— 那条验收永远在验别的东西。
      if (outs.indexOf('patch') >= 0 && Array.isArray(n.accept)) {
        for (let i = 0; i < n.accept.length; i++) {
          const a = n.accept[i];
          if (isObj(a) && a.type === 'cmd')
            warn('PATCH_CMD_ON_MAIN_TREE', at + 'accept[' + i + '] 是 cmd，但本节点只交 patch、不落盘：这条命令在**主树**上跑，看不见自己的 patch，验的是别的东西。验收挪到 integrate 或用 assert');
        }
      }
    }
    if (n.ctx !== undefined) {
      if (!isObj(n.ctx)) err('NODE_CTX', at + 'ctx 必须是对象');
      else {
        if (n.ctx.read !== undefined && (!Array.isArray(n.ctx.read) || n.ctx.read.some((r) => typeof r !== 'string' || !r)))
          err('NODE_CTX', at + 'ctx.read 必须是非空字符串数组（如 "src/a.ts#L10-80"）');
        if (n.ctx.from !== undefined && (!Array.isArray(n.ctx.from) || n.ctx.from.some((r) => typeof r !== 'string' || String(r).indexOf('.') < 1)))
          err('NODE_CTX', at + 'ctx.from 必须是 "id.字段" 形式的字符串数组');
      }
    }

    // accept —— §4：纯文字验收直接拒收；work 必须至少一条 cmd|assert。
    const acc = n.accept;
    if (!Array.isArray(acc) || acc.length === 0) {
      err('NODE_ACCEPT', at + '缺 accept：没有可执行的验收标准，纯文字验收拒收（§4）');
    } else {
      acc.forEach((a, i) => {
        const w = (msg) => err('NODE_ACCEPT', at + 'accept[' + i + '] ' + msg);
        if (!isObj(a)) return w('必须是对象');
        if (ACCEPT_TYPES.indexOf(a.type) < 0) return w('type 只能是 ' + ACCEPT_TYPES.join('/') + '，收到 ' + j(a.type));
        if (a.type === 'cmd' && (typeof a.run !== 'string' || !a.run)) return w('cmd 缺 run');
        if ((a.type === 'assert' || a.type === 'review') && (typeof a.rule !== 'string' || !a.rule)) return w(a.type + ' 缺 rule');
        if (a.type === 'review' && n.kind !== 'check') return w('review 型验收只准出现在 check 节点（§4），本节点是 ' + j(n.kind));

        // v2 · 规则 A（治 F1）：scope 断言的 allowlist 到底从哪来，必须能被解释器算出来。
        // 只有静态 writes、或显式 allow_from 动态集，才叫「可核」；两样都没有 = 这句断言在解释器里不存在。
        if (a.type === 'scope') {
          const hasWrites = Array.isArray(n.writes) && n.writes.length > 0;
          const hasFrom = Array.isArray(a.allow_from) && a.allow_from.length > 0;
          if (!hasWrites && !hasFrom)
            err('SCOPE_UNCHECKABLE', at + 'accept[' + i + '] 是 scope 断言，但节点既没有 writes 也没有 allow_from —— allowlist 算不出来，这句断言没法核（F1：文字规则与解释器脱节）');
        }
        if (a.allow_from !== undefined) {
          if (!Array.isArray(a.allow_from) || a.allow_from.length === 0) w('allow_from 必须是非空数组');
          else if (a.allow_from.some((r) => typeof r !== 'string' || String(r).split('.').length !== 2 || String(r).indexOf('.') < 1))
            w('allow_from 的元素必须是 "<上游id>.<字段>" 形式的字符串');
        }
        if (a.allow_under !== undefined) {
          if (!Array.isArray(a.allow_under) || a.allow_under.some((p) => typeof p !== 'string' || !p))
            w('allow_under 必须是非空字符串数组（路径前缀，如 "tests/"）');
          // 2026-11-10（EVIDENCE-GUARD C1）：动态 allowlist 的前缀同样不许圈进排除区 ——
          // 否则 `writes` 被拦住了，`allow_under` 又能把同一个洞绕回来。
          else
            for (const u of a.allow_under) {
              const hit = excludedTopOf(u);
              if (hit)
                err(
                  'SCOPE_EXCLUDED_PATH',
                  at + 'accept[' + i + '].allow_under 里的 ' + j(u) + ' 在快照排除区（' + hit + '/）内：' +
                    '动态 allowlist 会把这个前缀下的路径整片放行，而快照看不见它们（同 F6）',                );
            }
        }
        // 2026-11-09（F2）：Windows 上 `powershell -File x.ps1` 会被执行策略挡住
        // （`is not digitally signed` / `UnauthorizedAccess`）。E2E 里 `integ` 三轮全败的
        // **唯一共同原因**就是它 —— 落盘全做对了，验收命令跑不动。Gate 不会替你跑一遍命令，
        // 但这一条恰好是形状能判的：带 `-File` 就要求带 `-ExecutionPolicy`。
        // 实测：主树不带 Bypass → exit 1；副本不带 → exit 1；副本带 → exit 0（不是副本特有的）。
        if (a.type === 'cmd') {
          const s = String(a.run);
          const isPs = /(^|[\s"'\\/])(pwsh|powershell)(\.exe)?([\s"']|$)/i.test(s);
          if (isPs && /(^|\s)-file(\s|$)/i.test(s) && !/(^|\s)-executionpolicy(\s|$)/i.test(s)) {
            warn('CMD_NO_EXEC_BYPASS', at + 'accept[' + i + '] 是 `powershell -File ...` 但没有 `-ExecutionPolicy Bypass`：本机执行策略会把脚本拦在 `is not digitally signed`，这条命令必败、而 Gate 不会替你跑。写成 `powershell -NoProfile -ExecutionPolicy Bypass -File ...`');
          }
        }
      });
      const hard = acc.filter((a) => isObj(a) && (a.type === 'cmd' || a.type === 'assert'));
      if (n.kind === 'work' && hard.length === 0)
        err('NODE_ACCEPT', at + 'work 节点必须至少有 1 条 cmd 或 assert 验收（§4 的 Gate#4）');
      // 比 DESIGN 严一条：integrate 是唯一写手，它的落地动作必须有可执行验收（§8 Step 2「每打一个跑测试」）。
      if (n.kind === 'integrate' && hard.length === 0)
        err('NODE_ACCEPT', at + 'integrate 节点也必须至少有 1 条 cmd 或 assert（写手要能自证落地，§8 Step 2）');

      // v2 · 规则 C（治 F3）：check 的契约语义是「只读核查员」，这两条把它从口号变成判据。
      if (n.kind === 'check') {
        if (Array.isArray(n.writes) && n.writes.length > 0)
          err('CHECK_WRITES', at + 'kind=check 却声明了 writes（' + n.writes.join('、') + '）：只读核查员不该有写集。要写就换成 work / integrate');
        const hasCmd = acc.some((a) => isObj(a) && a.type === 'cmd');
        if (hasCmd)
          warn('CHECK_CMD_UNVERIFIED', at + 'accept 里有 cmd：Gate 判不了这条命令的副作用（会不会写文件），跑之前自己核一遍。这条不拒收');
        // v2.1（第 1 轮验收 N2）：两个方向都要提醒。只有上面那条时，经理为了「0 warn」
        // 会把 check 的 cmd 全删掉，只剩模型自评的 assert —— 那不是更好，是更差。
        else
          warn('CHECK_NO_EXECUTABLE', at + 'accept 里没有任何 cmd：核查结论全靠 assert / review 自评，没有可执行证据。给一条真正只读的命令（哈希比对 / diff / 只读统计）会更可信。这条不拒收');
      }
    }

    // 2026-11-09（F10）：`join=any` 允许上游失败，但 `ctx.from` 又硬引用那个上游的字段 ——
    // 上游一失败，下游就拿不到料。执行器（runner v2 起）会因此落 need_info（**不再自己发挥顶替**），
    // 但只要图这么写，这一片下游就会整片停住。Gate 会在跑之前提醒。
    if ((n.join || 'all') === 'any' && isObj(n.ctx) && Array.isArray(n.ctx.from) && Array.isArray(n.after)) {
      const deps = new Set(n.after);
      const hardRefs = n.ctx.from.filter((r) => typeof r === 'string' && deps.has(String(r).split('.')[0]));
      if (hardRefs.length) {
        warn('FROM_OPTIONAL_UPSTREAM', at + 'join=any 允许上游失败，但 ctx.from 又引用了 ' + hardRefs.join('、') + '：上游一失败这些字段就空，执行器会落 need_info（不会再自己发挥顶替）。要么改 join=all，要么别引用失败上游的产出');
      }
    }

    // on_fail —— §5.4：失败是静默的，必须显式声明。
    if (n.on_fail === undefined) {
      warn('NODE_ON_FAIL', at + '没声明 on_fail，按默认 {retry:0, then:"replan"} 走（§5.4 要求显式声明）');
    } else if (!isObj(n.on_fail)) {
      err('NODE_ON_FAIL', at + 'on_fail 必须是对象');
    } else {
      const f = n.on_fail;
      if (f.retry !== undefined && (!Number.isInteger(f.retry) || f.retry < 0)) err('NODE_ON_FAIL', at + 'on_fail.retry 必须是非负整数');
      if (f.escalate !== undefined) {
        if (!own(TIERS, f.escalate)) err('NODE_ON_FAIL', at + 'on_fail.escalate 未定义档位：' + j(f.escalate));
        else if (f.escalate === (n.tier || KIND_TIER[n.kind])) warn('NODE_ON_FAIL', at + 'on_fail.escalate 和本节点档位相同（' + f.escalate + '），升档等于没升');
      }
      if (f.then !== undefined && ['replan', 'skip', 'abort'].indexOf(f.then) < 0) err('NODE_ON_FAIL', at + 'on_fail.then 只能是 replan / skip / abort');
    }

    // when —— §4：只许对上游结构化输出的某字段做 eq / in / exists，不许 JS 表达式、不许模型判。
    if (n.when !== undefined) {
      const w = n.when;
      if (!isObj(w)) err('NODE_WHEN', at + 'when 必须是对象');
      else {
        if (!Array.isArray(n.after) || n.after.length === 0) err('NODE_WHEN', at + 'when 需要至少一个 after 才能引用上游');
        if (typeof w.ref !== 'string' || String(w.ref).split('.').length !== 2) err('NODE_WHEN', at + 'when.ref 必须是 "<上游id>.<字段>"');
        if (['eq', 'in', 'exists'].indexOf(w.op) < 0) err('NODE_WHEN', at + 'when.op 只能是 eq / in / exists，收到 ' + j(w.op));
        else if (w.op === 'in' && !Array.isArray(w.val)) err('NODE_WHEN', at + 'when.op=in 时 val 必须是数组');
        else if (w.op !== 'exists' && w.val === undefined) err('NODE_WHEN', at + 'when.op=' + w.op + ' 需要 val');
        if ((w.op === 'eq' || w.op === 'in') && typeof w.ref === 'string') {
          const f = String(w.ref).split('.')[1];
          if (FREE_TEXT.indexOf(f) >= 0)
            warn('WHEN_BRITTLE', at + 'when 对自由文本字段 ' + j(f) + ' 做 ' + w.op + '：模型换个说法分支就断，最好让上游吐可枚举的值');
        }
      }
    }
  }

  // ---- 第二遍：跨节点（要先收集完 byId）----
  for (const id of ids) {
    const n = byId[id];
    const after = Array.isArray(n.after) ? n.after : [];
    for (const d of after) {
      if (d === id) err('EDGE_SELF', id + '：after 里指到自己');
      else if (!own(byId, d)) err('EDGE_UNKNOWN', id + '：after 指向不存在的节点 ' + j(d));
    }
    if (n.ctx && Array.isArray(n.ctx.from)) {
      for (const f of n.ctx.from) {
        const parts = String(f).split('.');
        const src = parts[0];
        const field = parts.slice(1).join('.');
        if (!own(byId, src)) err('FROM_UNKNOWN', id + '：ctx.from 指向不存在的节点 ' + j(src));
        else if (field !== 'status' && !own(FIELDS, field)) err('FROM_FIELD', id + '：ctx.from 的字段 ' + j(field) + ' 不在输出字段库里');
        else if (field !== 'status' && !declares(byId[src], field)) err('FROM_UNDECLARED', id + '：ctx.from 要 ' + j(f) + '，但 ' + src + ' 的 out 里没声明 ' + field + '（schema 里没有的字段，模型根本回不出来）');
      }
    }

    // v2 · 规则 A 的一半：accept.allow_from 引用的上游字段必须算得出来（存在 / 是数组 / 上游声明过）。
    // 祖先关系要等祖先闭包，放在下面 !cycled 块里。
    for (const a of (Array.isArray(n.accept) ? n.accept : [])) {
      if (!isObj(a) || a.type !== 'scope' || !Array.isArray(a.allow_from)) continue;
      for (const ref of a.allow_from) {
        const parts = String(ref).split('.');
        const src = parts[0];
        const field = parts.slice(1).join('.');
        if (!own(byId, src)) err('SCOPE_FROM_UNKNOWN', id + '：accept.allow_from 指向不存在的节点 ' + j(src));
        else if (!own(FIELDS, field) || ARRAY_FIELDS.indexOf(field) < 0)
          err('SCOPE_FROM_NOT_ARRAY', id + '：allow_from 要 ' + j(ref) + '，但 ' + j(field) + ' 不是数组类字段（可用 ' + ARRAY_FIELDS.join('/') + '）—— 自由文本当路径清单永远匹配不上，会变成假越界');
        else if (!declares(byId[src], field)) err('SCOPE_FROM_UNDECLARED', id + '：allow_from 要 ' + j(ref) + '，但 ' + src + ' 的 out 里没声明 ' + field + '（模型回不出来，allowlist 会是空的）');
      }
    }
  }

  // ---- 成环 ----
  const color = {};
  const stack = [];
  let cycled = false;
  const dfs = (id) => {
    color[id] = 1;
    stack.push(id);
    const after = Array.isArray(byId[id].after) ? byId[id].after : [];
    for (const d of after) {
      if (!own(byId, d)) continue;
      if (color[d] === 1) {
        err('CYCLE', '成环：' + stack.slice(stack.indexOf(d)).concat([d]).join(' → '));
        cycled = true;
        return true;
      }
      if (!color[d] && dfs(d)) return true;
    }
    stack.pop();
    color[id] = 2;
    return false;
  };
  for (const id of ids) {
    if (!color[id] && dfs(id)) break;
  }

  // ---- 依赖祖先关系的检查（成环时跳过，祖先闭包没意义）----
  if (!cycled) {
    const anc = {};
    const ancestors = (id) => {
      if (anc[id]) return anc[id];
      anc[id] = {};
      for (const d of (Array.isArray(byId[id].after) ? byId[id].after : [])) {
        if (!own(byId, d)) continue;
        anc[id][d] = true;
        const up = ancestors(d);
        for (const k in up) anc[id][k] = true;
      }
      return anc[id];
    };
    for (const id of ids) ancestors(id);

    // 写集：两节点写集有交集、且互不为祖先 → 整张图拒收（§4，不许偷偷串行）
    for (let i = 0; i < ids.length; i++) {
      for (let k = i + 1; k < ids.length; k++) {
        const a = byId[ids[i]];
        const b = byId[ids[k]];
        const wa = Array.isArray(a.writes) ? a.writes : [];
        const wb = Array.isArray(b.writes) ? b.writes : [];
        if (!wa.length || !wb.length) continue;
        if (anc[ids[i]][ids[k]] || anc[ids[k]][ids[i]]) continue;
        const hit = [];
        for (const x of wa) for (const y of wb) if (norm(x) === norm(y)) hit.push(str(x));
        if (hit.length) err('WRITE_CONFLICT', ids[i] + ' 与 ' + ids[k] + ' 写集有交集且互不为祖先，不许偷偷串行：' + hit.join('、'));
      }
    }

    // 热文件只有 integrate 能写，别人只能提 requests（§4）
    for (const id of ids) {
      const n = byId[id];
      const ws = Array.isArray(n.writes) ? n.writes : [];
      for (const w of ws) {
        const h = hot.find((x) => x.key === norm(w));
        if (h && n.kind !== 'integrate')
          err('HOT_FILE_WRITE', id + '（' + n.kind + '）写了热文件 ' + h.raw + '：热文件只有 integrate 能写，改走 requests 由集成节点统一落');
      }
    }

    // when / from 的上游必须是祖先（不然取值时那节点可能还没跑）
    for (const id of ids) {
      const n = byId[id];
      if (isObj(n.when) && typeof n.when.ref === 'string') {
        const parts = n.when.ref.split('.');
        const src = parts[0];
        const field = parts[1];
        if (own(byId, src)) {
          if (!anc[id][src]) err('WHEN_NOT_ANCESTOR', id + '：when 引用的 ' + src + ' 不是它的祖先（取值时上游可能还没跑）');
          if (field !== 'status' && !own(FIELDS, field)) err('WHEN_FIELD', id + '：when.ref 的字段 ' + j(field) + ' 不在输出字段库里');
          else if (field !== 'status' && !declares(byId[src], field)) err('WHEN_UNDECLARED', id + '：when 要 ' + j(n.when.ref) + '，但 ' + src + ' 的 out 里没声明 ' + field);
        } else err('WHEN_UNKNOWN', id + '：when.ref 指向不存在的节点 ' + j(src));
      }
      if (n.ctx && Array.isArray(n.ctx.from)) {
        for (const f of n.ctx.from) {
          const src = String(f).split('.')[0];
          if (own(byId, src) && !anc[id][src]) err('FROM_NOT_ANCESTOR', id + '：ctx.from 引用的 ' + src + ' 不是它的祖先');
        }
      }
      // allow_from 也必须是祖先（取值时上游得先跑完）
      for (const a of (Array.isArray(n.accept) ? n.accept : [])) {
        if (!isObj(a) || a.type !== 'scope' || !Array.isArray(a.allow_from)) continue;
        for (const ref of a.allow_from) {
          const src = String(ref).split('.')[0];
          if (own(byId, src) && !anc[id][src]) err('SCOPE_FROM_NOT_ANCESTOR', id + '：accept.allow_from 引用的 ' + src + ' 不是它的祖先（allowlist 会在上游还没跑时就算）');
        }
      }
    }

    // v2 · 规则 A 的另一半（对齐 F1 真正的病灶）：scope 的 **rule 文字**里如果点名了
    // 「某个上游节点的某个字段」，那这句引用必须同时出现在 allow_from 里 —— 否则解释器算的
    // allowlist 与卡片上念给模型听的文字就是两套口径，模型照文字做必判 fail。
    for (const id of ids) {
      const n = byId[id];
      for (const a of (Array.isArray(n.accept) ? n.accept : [])) {
        if (!isObj(a) || a.type !== 'scope' || typeof a.rule !== 'string') continue;
        const froms = Array.isArray(a.allow_from) ? a.allow_from.map((r) => str(r)) : [];
        // v2.1（第 1 轮验收 N3）：正则放宽到含大写 / 连字符 / 数字的 id —— 旧写法只认小写，
        // 大写 id 会被静默漏掉。第一段是否真是节点 id 由下面的 own(byId, …) 精确裁决。
        const re = /([A-Za-z0-9_-]{1,64})\.([A-Za-z_][A-Za-z0-9_]*)/g;
        const bad = [];
        let m;
        while ((m = re.exec(a.rule)) !== null) {
          if (!own(byId, m[1])) continue; // 不是节点 id，当普通文件名放过（index.ts / vitest.config.ts 这类）
          const ref = m[1] + '.' + m[2];
          if (froms.indexOf(ref) < 0) bad.push(ref);
        }
        if (bad.length)
          err('SCOPE_RULE_UNCHECKED', id + '：scope 的 rule 文字引用了 ' + bad.join('、') + '，但 accept.allow_from 里没有它。这句话解释器不读 —— 照文字做会判 fail，照解释器做又违背文字（F1 就是这么来的）。要么把引用写进 allow_from，要么从 rule 文字里删掉');
      }
    }
  }

  // ---- v2 · 规则 C 的第二判据（治 F3）：check 节点跑的命令与写手**一字不差** ----
  // 判据刻意用「字面相同」而不是「猜副作用」：同一条命令不会因为在 check 节点上就变成只读。
  // 挡不住换写法（加个管道、换个参数就能绕），那是静态 Gate 的天花板，写在 DELIVERY 的「弱在哪」里。
  {
    const writerCmds = [];
    for (const id of ids) {
      const n = byId[id];
      if (!(Array.isArray(n.writes) && n.writes.length > 0) && n.kind !== 'integrate') continue;
      for (const a of (Array.isArray(n.accept) ? n.accept : [])) {
        if (isObj(a) && a.type === 'cmd' && typeof a.run === 'string') writerCmds.push({ id: id, run: a.run, key: cmdKey(a.run) });
      }
    }
    for (const id of ids) {
      const n = byId[id];
      if (n.kind !== 'check') continue;
      for (const a of (Array.isArray(n.accept) ? n.accept : [])) {
        if (!isObj(a) || a.type !== 'cmd' || typeof a.run !== 'string') continue;
        const k = cmdKey(a.run);
        const hit = writerCmds.find((w) => w.id !== id && w.key === k);
        if (hit)
          err('CHECK_REUSES_WRITER_CMD', id + '：kind=check 却跑了和写手 ' + hit.id + ' 一模一样的命令 `' + a.run + '` —— 同一条命令不会因为在 check 节点上就变成只读。要复核就换一条真正只读的命令（diff / 哈希比对 / 只读统计）');
      }
    }
  }

  // ---- v2 · 规则 B（治 F2）：预算口径 + 差额检查 ----
  // 名字是 max_calls 而不是 max_agents：执行器一直按「agent 调用次数」计数（含重试与升档），
  // 旧名字让 opus 那张真图写成了「7 个节点 → max_agents: 7」，然后一次重试就把末节点挤掉。
  if (isObj(G.budget) && G.budget.max_agents !== undefined) {
    err('BUDGET_RENAMED', 'budget.max_agents 已改名 budget.max_calls（语义不变：按 agent 调用次数计，含重试与升档，不是节点数）。旧名留着只会继续误导');
  } else if (isObj(G.budget) && Number.isFinite(G.budget.max_calls)) {
    const cap = G.budget.max_calls;
    let worst = 0;
    let loose = 0;
    for (const id of ids) {
      const n = byId[id];
      const f = isObj(n.on_fail) ? n.on_fail : {};
      const retry = Number.isInteger(f.retry) ? f.retry : 0;
      const base = n.tier || KIND_TIER[n.kind];
      const esc = f.escalate && own(TIERS, f.escalate) && f.escalate !== base ? 1 : 0;
      worst += 1 + retry + esc;
      loose += 1 + retry;
    }
    if (ids.length > cap)
      err('BUDGET_OVERFLOW', '节点数 ' + ids.length + ' > budget.max_calls=' + cap + '：连每个节点的第一次调用都发不完，后面的节点会被预算拦成 skipped。至少给到节点数 ' + ids.length + '（最坏情况要 ' + worst + '）');
    else if (worst > cap)
      warn('BUDGET_NO_MARGIN', '最坏情况（每步都重试并升档）要 ' + worst + ' 次调用，budget.max_calls=' + cap + ' 只够 ' + cap + '：跑到后面时重试/升档会被拒（不是静默跳节点，见执行器的预留名额）。不算升档是 ' + loose + ' 次。要么把 max_calls 提到 ' + worst + '，要么把 retry 调小 —— 不改也能跑，但别指望重试一定生效');
  }
  if (isObj(G.budget) && G.budget.max_usd !== undefined)
    warn('BUDGET_USD', 'budget.max_usd 无法核（引擎不回传 cost），只数 agent 次数');

  return { errs: errs, warns: warns, byId: byId, ids: ids };
}

// 上游是否在 out 里声明过这个字段（管道字段永远在 schema 里）
function declares(node, field) {
  if (PLUMBING.indexOf(field) >= 0) return true;
  return Array.isArray(node.out) && node.out.indexOf(field) >= 0;
}

// ---------------------------------------------------------------- 调度计划：waves + 关键路径

function plan(G, byId, ids) {
  const lvl = {};
  const level = (id) => {
    if (lvl[id] !== undefined) return lvl[id];
    const after = (Array.isArray(byId[id].after) ? byId[id].after : []).filter((d) => own(byId, d));
    const v = after.length ? 1 + Math.max.apply(null, after.map(level)) : 0;
    lvl[id] = v;
    return v;
  };
  ids.forEach(level);
  const waves = [];
  ids.forEach((id) => {
    const k = lvl[id];
    if (!waves[k]) waves[k] = [];
    waves[k].push(id);
  });

  // 关键路径：按节点数的最长链（v1 没有可靠的耗时估计）。并列时取输入顺序里先出现的。
  const len = {};
  const prev = {};
  const best = (id) => {
    if (len[id] !== undefined) return len[id];
    let m = 0;
    let p = null;
    for (const d of (Array.isArray(byId[id].after) ? byId[id].after : [])) {
      if (!own(byId, d)) continue;
      const v = best(d);
      if (v > m) {
        m = v;
        p = d;
      }
    }
    len[id] = m + 1;
    prev[id] = p;
    return len[id];
  };
  let tip = null;
  for (const id of ids) {
    const v = best(id);
    if (tip === null || v > len[tip]) tip = id;
  }
  const path = [];
  for (let c = tip; c; c = c && prev[c]) path.unshift(c);
  return { waves: waves, criticalPath: path, criticalLen: tip ? len[tip] : 0 };
}

// ---------------------------------------------------------------- 任务卡（短提示）

// v2（对齐 F1 的病灶）：scope 断言过去只把 rule 文字念给模型，而 scopeCheck 根本不读 a.rule ——
// 模型照文字做、解释器照 writes 判，双方各说各话。现在卡片里**同时**给出机器真正会核的 allowlist。
function describeAccept(a, node) {
  if (a.type === 'cmd') return '命令：`' + a.run + '`' + (a.expect ? '　期望：' + a.expect : '');
  if (a.type === 'assert') return '断言：' + a.rule;
  if (a.type === 'scope') {
    const src = [];
    if (Array.isArray(node.writes) && node.writes.length) src.push('writes 里的 ' + node.writes.length + ' 个文件（' + node.writes.join('、') + '）');
    if (Array.isArray(a.allow_from) && a.allow_from.length) src.push('外加 ' + a.allow_from.join('、') + ' 列出的路径');
    let s = '写集校验：' + (a.rule || 'changed ⊆ writes');
    s += '\n  ⚠️ 机器实际核的 allowlist = ' + (src.length ? src.join(' + ') : '（空 —— Gate 本会拒收这张图）');
    if (Array.isArray(a.allow_under) && a.allow_under.length) s += '；其中动态那部分必须落在 ' + a.allow_under.join('、') + ' 之下，越界部分会被剔除';
    s += '。changed 里出现任何不在 allowlist 里的路径 → 判 fail。上面那句文字只是说明，不参与判定';
    return s;
  }
  return '人工评审：' + a.rule + '（判断类，只抽样）';
}

const COMMON_RULES = [
  '缺信息就报 status=need_info，并在 missing 里逐条列出你要什么。**不许猜**——宁可多一轮往返，也不要一次猜错再返工。',
  '引用代码或结论必须给 file:line，并且自己打开看过；没看过的不要引用。',
  '只做这张卡上的事，别顺手重构、别追加没人要的东西。',
  '判不准的宁可不报，也不许编。',
];

function rulesFor(node) {
  if (node.kind === 'integrate')
    return [
      '你是本轮唯一的写手：只有你能落盘，别人只交 diff。',
      '一次只打一个上游 patch，每打完一个就跑一次卡里的命令；失败立刻停，报 status=fail，写清哪一步、什么输出。',
      '只许改 writes 里列的文件，一个都不许多；热文件归你，别人只能通过 requests 提。',
    ].concat(COMMON_RULES);
  if (node.kind === 'work')
    return [
      '不改主树：把改动做成 unified diff 放进 patch 字段（带 ---/+++ 与 @@ 行），**不要**用写文件工具落地。',
      '不要跑测试：卡里的命令由集成节点统一跑（v1 是并行读算、单点写入）。',
    ].concat(COMMON_RULES);
  if (node.kind === 'check')
    return [
      '只读：除了卡里 accept 列的命令之外，不许改、删、新建任何文件。',
      '结论要有证据：能指出 file:line 或命令输出的才写，判不准的写 need_info。',
    ].concat(COMMON_RULES);
  return ['只读：不许写、改、删任何文件，不许跑任何会改变状态的命令。'].concat(COMMON_RULES);
}

// 下游要用 verdict 做确定性分支时，**不能**把期望值写进任务卡：那等于告诉模型该选哪个
// 才能让分支过（leading the witness）。所以这里只给工程契约的通用词表，不点明哪条路由到哪。
function verdictHint(node) {
  if (!Array.isArray(node.out) || node.out.indexOf('verdict') < 0) return null;
  return '`verdict` 用本工程契约的约定词：pass / rework / escalate（判断类结论）。判不准就报 status=need_info，别硬凑一个 verdict 出来。';
}

function card(node, deps, depResults) {
  const L = [];
  L.push('你是本工程的' + (KIND_ROLE[node.kind] || '执行员') + '。工作目录就是本工程根目录，路径一律相对它写。');
  L.push('');
  L.push('## 任务卡 · ' + node.id + '（' + node.kind + '）');
  L.push('做什么：' + node.do);

  const ctx = isObj(node.ctx) ? node.ctx : {};
  if (Array.isArray(ctx.read) && ctx.read.length) L.push('只看这些位置（别全仓乱翻）：' + ctx.read.join('、'));

  if (deps.length) {
    L.push('');
    L.push('## 上游产物');
    const bySrc = {};
    if (Array.isArray(ctx.from)) {
      for (const f of ctx.from) {
        const parts = String(f).split('.');
        const field = parts.slice(1).join('.');
        if (!bySrc[parts[0]]) bySrc[parts[0]] = [];
        bySrc[parts[0]].push(field);
      }
    }
    deps.forEach((d, i) => {
      const r = depResults[i] || {};
      const fields = bySrc[d];
      if (fields && fields.length) {
        for (const f of fields) {
          if (f === 'status') L.push('- ' + d + '.status = ' + r.status);
          else if (r[f] === undefined) L.push('- ' + d + '.' + f + ' =（上游没回这个字段）');
          else L.push('- ' + d + '.' + f + ' =\n' + clip(r[f], node.kind === 'integrate' ? 8000 : 1200));
        }
      } else {
        if (r.artifacts && r.artifacts.length) L.push('- ' + d + ' 产物：' + r.artifacts.join('、'));
        if (r.summary) L.push('- ' + d + ' 摘要：' + clip(r.summary, 800));
      }
    });
  }

  // INC-20261110-01（C1）：`out` 含 patch 的节点**不落盘**，writes 对它的意思是「patch 只许指向哪些文件」。
  // 旧措辞「允许改动的文件只有…」会把它往落盘上引 —— 而落盘现在判 fs_write_forbidden（重试洗不掉）。
  const patchNode = Array.isArray(node.out) && node.out.indexOf('patch') >= 0;
  if (Array.isArray(node.writes) && node.writes.length) {
    if (patchNode) L.push('', 'patch 只许指向这些文件（patch_targets 必须落在里面；本节点自己一个文件都不落盘）：' + node.writes.join('、') + '。');
    else L.push('', '允许改动的文件只有：' + node.writes.join('、') + '。改动必须全部落在里面。');
  }
  if (patchNode) {
    // 措辞对齐 runner.py 的 build_prompt（同一张图两个执行器给员工的口径一致）
    L.push('', '## 字段语义（只交 patch，不碰主树）');
    L.push('- `patch`：你要交给下游的 unified diff 文本。交空会被判 `patch_missing`。');
    L.push('- `patch_targets`：这个 patch 会改到的文件（相对工程根，数组），必须落在允许清单内；交了 patch 却不填它会被判 `patch_targets_missing`，越界判 `patch_out_of_scope`。');
    L.push('- `changed`：**你本次实际落盘**改了哪些文件。只交 patch 的节点这里必须是 `[]` —— 报了落盘就判 `fs_write_forbidden`，**重试洗不掉**。');
  }
  if (Array.isArray(node.requests) && node.requests.length)
    L.push('要动热文件（' + node.requests.join('、') + '）只能提请求：写进 summary，不要给 diff。');

  if (Array.isArray(node.accept) && node.accept.length) {
    L.push('', '## 验收（' + (node.kind === 'work' ? '由集成节点执行，你不要自己跑' : '你自己先过一遍') + '）');
    for (const a of node.accept) L.push('- ' + describeAccept(a, node));
  }

  const vh = verdictHint(node);
  if (vh) L.push('', '## verdict 约定', vh);
  L.push('', '## 硬规则');
  for (const r of rulesFor(node)) L.push('- ' + r);
  L.push('', '输出严格按 schema 的 JSON：status 只能是 ok / fail / need_info。');
  return L.join('\n');
}

// ---------------------------------------------------------------- 结构化输出 schema

function nodeSchema(node) {
  const props = {
    status: { type: 'string', enum: ['ok', 'fail', 'need_info'], description: 'ok=做完了 / fail=做不了 / need_info=缺信息要人补' },
  };
  const want = PLUMBING.slice();
  if (Array.isArray(node.out)) for (const f of node.out) if (want.indexOf(f) < 0) want.push(f);
  // INC-20261110-01 第 1 轮 M1（验收官挖出来的，我原先没看见）：schema 是 additionalProperties:false，
  // 而 PLUMBING 只有 summary/missing/artifacts。`out` 声明了 patch 却没声明 changed 的节点，
  // 在真引擎里**根本交不出** changed 字段 → C1 的 fs_write_forbidden 永远没有输入（空判据）。
  // 这与 E2EFIX B1「输入形态让判据静默跳过」是同一类问题。卡面本来就要求 patch 节点报 changed=[]，
  // 所以这里把它无条件放进 patch 节点的 schema —— 让「老实报我没落盘」在结构上可行。
  // ⚠️ 2026-11-10 纠错（INC-20261110-01 第 2 轮验收官 N1）：这里原先写着「假 agent 绕过 schema，所以 harness
  // 看不出这个洞；真引擎会」—— **这句是错的**。`tasks/DAG-2026-11-06/harness.mjs` 的 run() 会把 opts
  // （含 opts.schema）原样透传给假 agent，plan() 收得到，零成本就能断言。守护断言见
  // `exec/schema-patch-changed`（删掉下面这行 `want.push('changed')` 它就该红）。
  if (Array.isArray(node.out) && node.out.indexOf('patch') >= 0 && want.indexOf('changed') < 0) want.push('changed');
  for (const f of want) if (own(FIELDS, f) && !own(props, f)) props[f] = FIELDS[f];
  return { type: 'object', additionalProperties: false, required: ['status', 'summary'], properties: props };
}

// ---------------------------------------------------------------- 入参解析

let G = isObj(args) ? args.graph : null;
if (isObj(args) && typeof args.graphJson === 'string' && args.graphJson.trim()) {
  try {
    G = JSON.parse(args.graphJson);
  } catch (e) {
    return { mode: 'gate', ok: false, errs: [{ code: 'GRAPH_JSON', msg: 'graphJson 解析失败：' + str(e && e.message) }], warns: [] };
  }
}

const dryRun = isObj(args) && args.dryRun === true;

// ---- 对照矩阵模式（只给 dry-run 用）：一组图逐张过 Gate ----
if (isObj(args) && Array.isArray(args.graphs)) {
  if (!dryRun) return { mode: 'gate', ok: false, errs: [{ code: 'BATCH_NOT_DRYRUN', msg: 'args.graphs 只支持 args.dryRun=true（批量真跑请逐张调）' }], warns: [] };
  const cases = args.graphs.map((g, i) => {
    const name = isObj(g) && typeof g.case === 'string' ? g.case : 'case-' + (i + 1);
    const graph = isObj(g) && g.graph !== undefined ? g.graph : g;
    const v = validate(graph);
    const one = { name: name, ok: v.errs.length === 0, errs: v.errs, warns: v.warns };
    if (one.ok) {
      const P = plan(graph, v.byId, v.ids);
      one.waves = P.waves;
      one.criticalPath = P.criticalPath;
    } else {
      one.codes = v.errs.map((e) => e.code);
    }
    return one;
  });
  return { mode: 'dry-run-batch', ok: cases.every((c) => c.ok), agents: 0, cases: cases };
}

// ---- dry-run：只出 Gate 结果 + 调度计划，一个 agent 都不调 ----
const V = validate(G);
if (dryRun) {
  const out = {
    mode: 'dry-run',
    ok: V.errs.length === 0,
    plan_id: isObj(G) && typeof G.plan_id === 'string' ? G.plan_id : null,
    agents: 0,
    errs: V.errs,
    warns: V.warns,
  };
  if (out.ok) {
    const P = plan(G, V.byId, V.ids);
    out.nodeCount = V.ids.length;
    out.waves = P.waves;
    out.waveCount = P.waves.length;
    out.criticalPath = P.criticalPath;
    out.criticalLen = P.criticalLen;
    out.schedule = V.ids.map((id) => ({
      id: id,
      kind: V.byId[id].kind,
      tier: V.byId[id].tier || KIND_TIER[V.byId[id].kind] || 'work',
      after: (Array.isArray(V.byId[id].after) ? V.byId[id].after : []).slice(),
      writes: (Array.isArray(V.byId[id].writes) ? V.byId[id].writes : []).slice(),
      accept: (Array.isArray(V.byId[id].accept) ? V.byId[id].accept : []).map((a) => (isObj(a) ? a.type : '?')),
    }));
    out.tiers = {};
    for (const k in TIERS) out.tiers[k] = TIERS[k].provider + '/' + TIERS[k].model + (TIERS[k].channel === 'official' ? '' : '（替代档）');
    out.note = '一个 agent 都没调。人对着 waves 核一遍，点头之后再把 dryRun 拿掉真跑。';
  } else {
    out.note = 'Gate 拒收，真跑模式下连一个 agent 都不会起。';
  }
  return out;
}

// ---- 真跑：Gate 不过就整图拒收，0 个 agent ----
if (V.errs.length) {
  return {
    mode: 'run',
    ok: false,
    rejected: true,
    plan_id: isObj(G) && typeof G.plan_id === 'string' ? G.plan_id : null,
    agents_started: 0,
    errs: V.errs,
    warns: V.warns,
    note: 'Gate 拒收：整张图没跑，一个 agent 都没调。',
  };
}

// ---------------------------------------------------------------- 执行器

const { byId, ids } = V;
const P0 = plan(G, byId, ids);

const results = {}; // id → 结果（body 解析后回填，祖先一定先于后代写进去）
const memo = {}; // id → promise（§5.2：每节点一个 promise，不做栅栏）
let started = 0;
let aborted = false;
const replan = [];
const maxCalls = isObj(G.budget) && Number.isFinite(G.budget.max_calls) ? G.budget.max_calls : null;

// v2（治 F2）：每个节点先预留一次首发名额，重试与升档只能吃余量。
// 这样「一次重试把末节点挤成 skipped」在机制上不会发生；而且「哪些节点被跳过」不再取决于并行时序
// （旧实现里谁先抢到预算谁先跑，同一张图两次跑可能跳掉不同的节点）。
const firstStarted = {};
const pendingFirst = () => ids.reduce((s, x) => s + (own(firstStarted, x) ? 0 : 1), 0);

function record(id, status, extra) {
  const node = byId[id];
  const e = extra || {};
  const r = {
    id: id,
    kind: node.kind,
    status: status,
    tier: e.tier || node.tier || KIND_TIER[node.kind] || 'work',
    attempts: e.attempts || 0,
  };
  const payload = isObj(e.payload) ? e.payload : {};
  const keys = ['summary', 'missing', 'artifacts', 'changed', 'facts', 'iface', 'verdict', 'patch', 'notes'];
  if (Array.isArray(node.out)) for (const f of node.out) if (keys.indexOf(f) < 0) keys.push(f);
  for (const k of keys) {
    if (payload[k] === undefined || payload[k] === null) continue;
    // O4（2026-11-10）：这里**存全文**。原先截到 1200 再进 results，于是下游 integrate 拿到的是残缺 diff
    // —— 与文件头第 6 条「下游 integrate 节点拿到的是全文」直接相反，真图那次 F10「integ 自己现写 122 行」
    // 就有这个嫌疑。截断只发生在**返回体**（见文件末尾 shrinkForReturn），对外契约不变。
    if (k === 'patch') r.patch = payload.patch;
    else r[k] = payload[k];
  }
  r.error = e.error === undefined ? null : e.error;
  r.skipped_because = e.why === undefined ? null : e.why;
  if (e.scope) r.scope = e.scope;
  // INC-20261110-01：C2 落的 need_info 单独一个字段，别和 skipped_because 混（「缺料」≠「被挡」）
  if (e.needInfo) r.need_info_because = e.needInfo;
  // C1：最后一次 attempt 的 patch 判据、逐 attempt 记录、跨 attempt 累积的不可洗伤害
  if (e.patch) r.patch_check = e.patch;
  if (Array.isArray(e.patchLog) && e.patchLog.length) r.patch_attempts = e.patchLog;
  if (Array.isArray(e.harm) && e.harm.length) r.harm = e.harm.slice();
  return r;
}

const skip = (id, why) => record(id, 'skipped', { why: why });

// 比较前只做「首尾空白 + 大小写」归一：一个 "Pass" 不该把分支断掉。
// 但措辞差异（"pass" vs "通过"）治不了 —— 那是 WHEN_BRITTLE 提醒的领域。
const cmp = (x) => (typeof x === 'string' ? x.trim().toLowerCase() : x);

function evalWhen(w, val) {
  if (w.op === 'exists') return val !== undefined && val !== null && val !== '';
  if (w.op === 'in') return Array.isArray(w.val) && w.val.map(cmp).indexOf(cmp(val)) >= 0;
  return cmp(val) === cmp(w.val);
}

// 写集校验：allowlist = 静态 writes ∪ accept.allow_from 取得的动态路径（受 allow_under 约束）。
// 动态部分是「上游模型说了算」的，所以：上游不是 ok 或字段缺失 → 按空集处理（fail-closed）；
// 落在 allow_under 之外的路径直接剔除并记进 dropped（不给它默默扩权）。
// INC-20261110-01：allowlist 的计算从 scopeCheck 里原样抽出来，给 patchCheck 共用 ——
// patch_out_of_scope 与 scope 越界必须用**同一份** allowlist，否则又是两套口径。
// （与 runner.py `allow_set()` 的差别：这里沿用解释器自己的 allow_under 过滤，runner 不过滤 —— 见 VERIFY 越界观察）
function allowOf(node) {
  const wants = (Array.isArray(node.accept) ? node.accept : []).filter((a) => isObj(a) && a.type === 'scope');
  const allow = [];
  if (Array.isArray(node.writes)) for (const w of node.writes) allow.push(norm(w));
  const dropped = [];
  for (const a of wants) {
    if (!Array.isArray(a.allow_from)) continue;
    const prefixes = Array.isArray(a.allow_under) && a.allow_under.length ? a.allow_under.map(norm) : null;
    for (const ref of a.allow_from) {
      const parts = String(ref).split('.');
      const src = parts[0];
      const field = parts.slice(1).join('.');
      const r = own(results, src) ? results[src] : null;
      const v = r && r.status === 'ok' ? r[field] : undefined;
      if (!Array.isArray(v)) continue;
      for (const p of v) {
        const k = norm(p);
        if (prefixes && !prefixes.some((pre) => k === pre || k.indexOf(pre + '/') === 0)) {
          dropped.push(str(p));
          continue;
        }
        if (allow.indexOf(k) < 0) allow.push(k);
      }
    }
  }
  return { allow: allow, dropped: dropped };
}

function scopeCheck(node, payload) {
  const wants = (Array.isArray(node.accept) ? node.accept : []).filter((a) => isObj(a) && a.type === 'scope');
  if (!wants.length) return null;
  const { allow, dropped } = allowOf(node);
  const base = { allowlist: allow.length, dropped: dropped };
  // v2.1（第 1 轮验收 N1）：空 allowlist 过去一律 ok:null（=「没核」），节点照样记 ok —— 与注释写的
  // fail-closed 相反。新真图的 w_docs 正是这个形状（没有 writes，只有 allow_from）。
  // 现在的口径：allowlist 为空时，**有改动就判越界**，没改动才记「没核」。
  if (!allow.length) {
    if (Array.isArray(payload.changed) && payload.changed.length > 0)
      return Object.assign(
        { ok: false, outside: payload.changed.slice(), why: 'allowlist 为空（writes 与 allow_from 都没给出可核的路径），但节点报了 changed —— 按 fail-closed 一律判越界' },
        base
      );
    return Object.assign({ ok: null, why: 'allowlist 为空且没有 changed，视为没改动（没核）' }, base);
  }
  if (!Array.isArray(payload.changed)) return Object.assign({ ok: null, why: '节点没回 changed，scope 无法核（v1 不解析 diff）' }, base);
  const outside = payload.changed.filter((c) => allow.indexOf(norm(c)) < 0);
  return Object.assign({ ok: outside.length === 0, outside: outside }, base);
}

// ---------------------------------------------------------------- C1 / C2（INC-20261110-01，对齐 runner.py）

// 路径字段的输入形态归一：数组 → 取其中的字符串/数字；非空字符串 → 单元素数组；其它 → 空。
// 对齐 runner.py `compare()` 对 changed / patch_targets 的处理 —— E2EFIX 第 1 轮 B1 的洞就是
// 「只认 list，写成字符串时整条判据被静默跳过」。**缺失 ≠ 合法，字符串 ≠ 跳过。**
function pathList(x) {
  if (Array.isArray(x)) return x.filter((v) => typeof v === 'string' || typeof v === 'number').map((v) => String(v));
  if (typeof x === 'string' && x.trim()) return [x];
  return [];
}

const PATCH_KEYS = ['fs_write_forbidden', 'patch_missing', 'patch_targets_missing', 'patch_out_of_scope'];

// C1：`out` 声明了 patch 的节点「只交 diff、不碰主树」（DESIGN.md:203）。四条判据，对齐 runner.py `compare()`：
//   ① fs_write_forbidden    —— 报了落盘（changed 非空）。**解释器没有文件系统，只能读自报的 changed**；
//                              runner 读的是真实快照差。所以这里抓得住「老实报了落盘的」，抓不住「落了盘却不报的」。
//   ② patch_missing         —— patch 缺失 / 非字符串 / 全空白。
//   ③ patch_targets_missing —— 交了 patch 却说不出会改到哪（patch_out_of_scope 没有输入 = 空判据）。
//   ④ patch_out_of_scope    —— patch_targets ⊄ allowlist（与 scopeCheck 同一份 allowOf）。
// 返回 null = 本节点不是 patch 节点（判据不适用）；否则返回四个键 + hit + ok。
// **每一次 attempt 都要过这一关**（不论自报 ok / fail / need_info / agent 抛错）——
// 放在 attempt 循环里、紧跟在拿到结果之后，是 body() 里所有 attempt 唯一共同经过的位置（E2EFIX B3 的教训）。
function patchCheck(node, res) {
  if (!Array.isArray(node.out) || node.out.indexOf('patch') < 0) return null;
  const pay = isObj(res) ? res : {};
  const c = { fs_write_forbidden: [], patch_missing: [], patch_targets_missing: [], patch_out_of_scope: [] };
  const patchOk = typeof pay.patch === 'string' && pay.patch.trim() !== '';
  if (!patchOk) c.patch_missing = ['patch'];
  const tg = pathList(pay.patch_targets);
  if (patchOk && !tg.length) c.patch_targets_missing = ['patch_targets'];
  const allow = allowOf(node).allow;
  c.patch_out_of_scope = tg.filter((t) => allow.indexOf(norm(t)) < 0);
  c.fs_write_forbidden = pathList(pay.changed);
  const hit = PATCH_KEYS.filter((k) => c[k].length > 0);
  return Object.assign({ ok: hit.length === 0, hit: hit, fs_source: 'payload.changed（自报）', allowlist: allow.length }, c);
}

const patchWhy = (pc) => pc.hit.map((k) => k + '=' + pc[k].join(',')).join('；');

// C2：`ctx.from` 的硬依赖，对齐 runner.py `from_block_reason()`。
// 引用的 `上游.字段`，上游**没 ok** 且该字段没有值 → 本节点不许起跑，落 need_info（缺料），不是 skipped（被挡）。
// 「没 ok 的上游」的字段**一律视为没有值**：runner 的 results 只收 ok 节点，失败节点即使回了 patch 也不往下传 ——
// 宁可停，也不让下游在没通过验收的产物上继续干（E2E 里 integ 现写 122 行的直接对策）。
// 唯一例外是 `.status`：上游的状态本身就是值（Gate 允许引用它、card 会照实打印）。runner 在这一点上会照样拦 —— 见 VERIFY 越界观察。
function fromBlockReason(node) {
  const refs = isObj(node.ctx) && Array.isArray(node.ctx.from) ? node.ctx.from : [];
  const missing = [];
  for (const ref of refs) {
    if (typeof ref !== 'string' || ref.indexOf('.') < 1) continue;
    const parts = ref.split('.');
    const src = parts[0];
    const field = parts.slice(1).join('.');
    if (!own(results, src)) continue; // 防呆：Gate 已保证是祖先，祖先一定先于本节点写进 results
    const r = results[src];
    if (r.status === 'ok') continue;
    if (field === 'status') continue;
    missing.push(ref + '（上游 status=' + r.status + '）');
  }
  if (!missing.length) return null;
  return { why: 'ctx.from 的上游产物缺失：' + missing.join('、') + ' —— 不许自己发挥顶替，落 need_info 等上游重跑', missing: missing };
}

function normStatus(res) {
  if (!isObj(res)) return 'fail';
  const s = str(res.status).toLowerCase().replace(/[-\s]/g, '_');
  if (s === 'ok' || s === 'pass' || s === 'done') return 'ok';
  if (s === 'need_info' || s === 'needinfo') return 'need_info';
  return 'fail';
}

function runNode(id) {
  if (memo[id]) return memo[id];
  // 先把 promise 记进 memo 再跑 body：DAG 里不会有真回环，但这样连递归引用也炸不了栈
  const p = Promise.resolve()
    .then(() => body(id))
    .then((r) => {
      results[id] = r;
      return r;
    });
  memo[id] = p;
  return p;
}

async function body(id) {
  const node = byId[id];
  const deps = (Array.isArray(node.after) ? node.after : []).filter((d) => own(byId, d));
  const depResults = await Promise.all(deps.map(runNode));

  if (aborted) return skip(id, 'on_fail.then=abort 已触发，后续节点不再起跑');

  // join：all（默认）要求上游全 ok；any 只要有 1 个 ok
  const join = node.join === 'any' ? 'any' : 'all';
  const okFlags = deps.map((d, i) => depResults[i].status === 'ok');
  const depLine = deps.map((d, i) => d + '=' + depResults[i].status).join('、');
  if (deps.length) {
    if (join === 'all' && okFlags.some((v) => !v)) return skip(id, '上游未全 ok（join=all）：' + depLine);
    if (join === 'any' && !okFlags.some((v) => v)) return skip(id, '上游没有一个 ok（join=any）：' + depLine);
  }

  // when：确定性分支，只许对上游字段做 eq / in / exists
  if (isObj(node.when)) {
    const w = node.when;
    const parts = String(w.ref).split('.');
    const src = parts[0];
    const field = parts.slice(1).join('.');
    const r = own(results, src) ? results[src] : null;
    if (!r) return skip(id, 'when 引用的 ' + src + ' 没有结果');
    let val;
    if (field === 'status') val = r.status;
    else if (r.status !== 'ok') return skip(id, 'when 依赖的 ' + src + ' 不是 ok（' + r.status + '），取不到 ' + field);
    else val = r[field];
    if (!evalWhen(w, val)) return skip(id, 'when 不成立：' + w.ref + ' ' + w.op + ' ' + j(w.val) + '，实际 ' + j(val));
  }

  // C2（INC-20261110-01）：ctx.from 硬依赖。顺序对齐 runner.py `gate_node()`：abort → join → when → **from** → 首发预算。
  // 放在预算之前 —— 缺料的节点压根不该占一个名额；落 need_info，不走 on_fail.then（runner 同）。
  const fb = fromBlockReason(node);
  if (fb) return record(id, 'need_info', { needInfo: fb.why, payload: { summary: fb.why, missing: fb.missing } });

  const baseTier = node.tier || KIND_TIER[node.kind] || 'work';
  const onFail = isObj(node.on_fail) ? node.on_fail : {};
  const retry = Number.isInteger(onFail.retry) ? onFail.retry : 0;
  const tiers = [baseTier];
  if (onFail.escalate && TIERS[onFail.escalate] && onFail.escalate !== baseTier) tiers.push(onFail.escalate);

  const prompt = card(node, deps, depResults);
  const schema = nodeSchema(node);

  let attempts = 0;
  let last = null;
  let budgetStop = false;
  // C1：跨 attempt 累积的**不可洗**伤害（E2EFIX B2：attempt 1 写主树、attempt 2 只交 patch，不许因此判 ok）
  const harm = [];
  // N4（第 1 轮验收官）：harm 现在有两个来源（patch 判据 / scope 越界），文案不该一律写成 fs_write_forbidden。
  // 只用于失败文案，不进结果契约（结果里仍是路径数组）。
  const harmSrc = [];
  let harmStop = false;
  const patchLog = [];
  // O3（2026-11-10）：最近一次 attempt 的 scope 检查结果 —— 越界时要能被 tail 的证据带走
  let scopeLast = null;

  for (let ti = 0; ti < tiers.length; ti++) {
    const tier = tiers[ti];
    const tries = ti === 0 ? 1 + retry : 1;
    for (let k = 0; k < tries; k++) {
      if (budgetStop) break;
      if (aborted) return skip(id, 'on_fail.then=abort 已触发');
      if (maxCalls !== null) {
        const pending = pendingFirst(); // 含自己（还没起跑时算进去）
        if (attempts === 0) {
          // 首发：必须保证自己和所有还没起跑的兄弟都还留得下名额
          if (started + pending > maxCalls)
            return skip(id, 'budget.max_calls 不够给每个节点留首发名额（已用 ' + started + '，还要给 ' + pending + ' 个未起跑节点留，上限 ' + maxCalls + '）');
        } else if (started + pending >= maxCalls) {
          // 重试 / 升档：只能吃余量，不许挤掉别人的首发
          log(id + '：预算余量只够首发（已用 ' + started + ' / 上限 ' + maxCalls + '），第 ' + (attempts + 1) + ' 次调用（重试/升档）被拒');
          budgetStop = true;
          break;
        }
      }
      started += 1;
      attempts += 1;
      firstStarted[id] = true;

      let res = null;
      let fail = null;
      try {
        res = await agent(prompt, {
          label: id,
          phase: node.kind,
          schema: schema,
          provider: TIERS[tier].provider,
          model: TIERS[tier].model,
        });
      } catch (e) {
        const code = str(e && e.code);
        // 脚本自己写错（选项/ schema / 参数 / 超限）→ 立刻炸，别当成节点失败咽下去
        if (FATAL.indexOf(code) >= 0) throw e;
        // 其余（AGENT_RESULT / AGENT_START / 其它）按 F4 就地接住，不让它炸图
        fail = (code ? code + '：' : '') + str(e && e.message ? e.message : e);
      }

      const st0 = fail ? 'fail' : normStatus(res);
      // C1：每次 attempt 都判（自报 ok / fail / need_info / agent 抛错都经过这里）。自报 ok 但 patch 判据没过 → 按 fail 算。
      const pc = patchCheck(node, res);
      // O3（2026-11-10）：scope 也必须**每个 attempt** 判，而且越界要算进不可洗的 harm。
      // 原先只在循环结束后对「最后一次 + status=ok」的结果判 scope，于是
      // 「第 1 次 attempt 越界、第 2 次干净成功」能把越界洗掉 —— 与 E2EFIX B2（fs_write_forbidden 没进 harm）同型。
      const scNow = fail ? null : scopeCheck(node, res);
      if (scNow) scopeLast = scNow;
      if (scNow && scNow.ok === false) {
        for (const p of scNow.outside) if (harm.indexOf(p) < 0) harm.push(p);
        if (harmSrc.indexOf('scope_outside') < 0) harmSrc.push('scope_outside');
      }
      let why = fail;
      if (pc) {
        patchLog.push({ attempt: attempts, tier: tier, self_status: st0, hit: pc.hit.slice() });
        for (const p of pc.fs_write_forbidden) if (harm.indexOf(p) < 0) harm.push(p);
        if (pc.fs_write_forbidden.length && harmSrc.indexOf('fs_write_forbidden') < 0) harmSrc.push('fs_write_forbidden');
        if (!pc.ok && !why) why = 'patch 判据未过：' + patchWhy(pc) + (st0 === 'ok' ? '' : '（自报 status=' + st0 + '）');
      }
      const st = st0 === 'ok' && pc && !pc.ok ? 'fail' : st0;
      last = { tier: tier, res: res, status: st, fail: why, patch: pc };

      // 停止顺序对齐 runner.py：harm 先于 need_info / 重试 —— 已经发生的损害不再浪费调用去「洗」
      if (harm.length) {
        harmStop = true;
        log(id + '：不可洗的越界（' + harm.join('、') + '）→ 停止重试/升档');
        break;
      }
      if (st === 'ok') break;
      if (st === 'need_info') {
        log(id + '：报 need_info，跳过同档重试直接升档（§4）');
        break;
      }
      if (k < tries - 1) log(id + '：' + tier + ' 档第 ' + (k + 1) + ' 次失败，同档重试');
    }
    // 只有 ok 才停：need_info 要按 §4 升档再问一次（同档重试已经跳过了）
    if (harmStop) break;
    if (budgetStop) break;
    if (last && last.status === 'ok') break;
    if (ti < tiers.length - 1) log(id + '：' + tier + ' 档没成（' + (last ? last.status : '?') + '）→ 升 ' + tiers[ti + 1] + ' 档');
  }

  // 预算把重试/升档掐掉时，失败原因要如实写在节点结果里（别让它看起来像「模型自己挂了」）
  if (budgetStop) {
    const prev = last && last.fail ? last.fail : last ? 'status=' + last.status : '没有拿到结果';
    last = {
      tier: last ? last.tier : baseTier,
      res: last ? last.res : null,
      status: 'fail',
      fail: prev + '；且 budget.max_calls 的余量只够首发，重试/升档被拒（' + started + '/' + maxCalls + '）',
      patch: last ? last.patch : null,
    };
  }

  // C1 的证据随节点结果带出去（三个出口 ok / need_info / tail 都带，不只失败那条）
  // O3（2026-11-10）：harm 现在不只来自 patch 节点（scope 越界也算不可洗），所以不再挂在 `last.patch` 条件上。
  const pext = Object.assign(
    {},
    last && last.patch ? { patch: last.patch, patchLog: patchLog } : {},
    harm.length ? { harm: harm } : {},
    scopeLast ? { scope: scopeLast } : {}
  );

  // 失败收尾（含 scope 越界）：一律走 on_fail.then，别让任何一种失败绕过它
  const then = ['skip', 'abort', 'replan'].indexOf(onFail.then) >= 0 ? onFail.then : 'replan';
  const tail = (why, tier, payload, extra) => {
    // then=skip 时把 extra（patch 判据 / harm / scope）一起带上 —— 否则「写过主树」会被 skipped 盖掉、查无痕迹
    if (then === 'skip') return record(id, 'skipped', Object.assign({ why: 'on_fail.then=skip：' + why }, extra || {}));
    if (then === 'abort') {
      aborted = true;
      log(id + '：on_fail.then=abort → 后续节点不再起跑（已在跑的不会被取消）');
    }
    if (then === 'replan') replan.push(id);
    return record(id, 'fail', Object.assign({ tier: tier, attempts: attempts, payload: payload, error: why + '；then=' + then }, extra || {}));
  };

  // C1：不可洗伤害优先于一切终态（对齐 runner.py：stop=harm → why_fail「写主树（不可用重试洗掉）」）
  // N4：来源按 harmSrc 点名（fs_write_forbidden / scope_outside），不再一律写成 fs_write_forbidden。
  if (harm.length)
    return tail('越界（' + (harmSrc.join('/') || '未知来源') + '，**不可用重试洗掉**）：' + harm.join('、'), last.tier, last.res, pext);

  if (last && last.status === 'ok') {
    // N4：这里原先还有一条 `if (sc.ok === false) return tail('scope 越界…')` —— **已删**。
    // 它现在走不到：越界在 attempt 循环里就 push 进 harm 并 break，上面那条 harm 分支会先命中。
    // 删掉是因为「删了也没断言红」的死代码会掩盖真问题（第 1 轮验收官 N4 实测：删掉 0 红）。
    const sc = scopeCheck(node, last.res);
    return record(id, 'ok', Object.assign({ tier: last.tier, attempts: attempts, payload: last.res }, pext, { scope: sc }));
  }
  if (last && last.status === 'need_info')
    return record(id, 'need_info', Object.assign({ tier: last.tier, attempts: attempts, payload: last.res }, pext));

  return tail(
    last ? last.fail || 'status=' + last.status + '（' + last.tier + ' 档）' : '没有拿到任何结果',
    last ? last.tier : baseTier,
    last ? last.res : null,
    pext
  );
}

// ---------------------------------------------------------------- 跑

log('Gate 通过：' + ids.length + ' 个节点 / ' + P0.waves.length + ' 个波次 / 关键路径 ' + P0.criticalLen + ' 节点：' + P0.criticalPath.join(' → '));
log('档位：' + ids.map((id) => id + '=' + (byId[id].tier || KIND_TIER[byId[id].kind])).join(' '));

const all = await Promise.all(ids.map(runNode));

// 汇总
const counts = { nodes: ids.length, ok: 0, fail: 0, skipped: 0, need_info: 0 };
const usedTiers = [];
const touched = [];
for (const r of all) {
  if (counts[r.status] !== undefined) counts[r.status] += 1;
  if (r.attempts > 0 && usedTiers.indexOf(r.tier) < 0) usedTiers.push(r.tier);
  if (Array.isArray(r.changed)) for (const c of r.changed) if (touched.indexOf(c) < 0) touched.push(c);
}
const degraded = usedTiers
  .filter((t) => TIERS[t] && TIERS[t].channel !== 'official')
  .map((t) => ({ tier: t, why: TIERS[t].why }));

// O4（2026-11-10）：对外返回体仍把 patch 截到 1200（文件头第 6 条，契约不变），但内部 results 是全文。
// `patch_chars` 记原文长度 —— 否则从返回体上看不出「本来就 1200 字」与「被削成 1200 字」的区别。
const shrinkForReturn = (r) =>
  typeof r.patch === 'string'
    ? Object.assign({}, r, { patch: clip(r.patch, 1200), patch_chars: r.patch.length })
    : r;

return {
  mode: 'run',
  ok: counts.ok === ids.length,
  plan_id: isObj(G) && typeof G.plan_id === 'string' ? G.plan_id : null,
  counts: counts,
  agents_started: started,
  waves: P0.waves,
  critical_path: P0.criticalPath,
  aborted: aborted,
  replan_needed: replan,
  degraded_tiers: degraded,
  files_touched: touched,
  // INC-20261110-01：C2 落的 need_info（缺料）与 C1 的不可洗伤害单独列出，汇总里一眼能分清
  need_info_blocked: all.filter((r) => r.need_info_because).map((r) => ({ id: r.id, why: r.need_info_because })),
  patch_harm: all.filter((r) => Array.isArray(r.harm) && r.harm.length).map((r) => ({ id: r.id, status: r.status, harm: r.harm })),
  budget: {
    max_calls: maxCalls,
    max_usd: isObj(G.budget) && Number.isFinite(G.budget.max_usd) ? G.budget.max_usd : null,
    usd_tracked: false,
  },
  gates: { errs: [], warns: V.warns },
  nodes: all.map(shrinkForReturn),
};
