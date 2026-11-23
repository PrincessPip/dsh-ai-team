/**
 * 路径解析与**引擎自检**（SPEC §4 的 `engineDir` / `pythonPath` / `nodePath` / `outDirName`）。
 *
 * 设计原则：
 * - 一切路径都从 `import.meta.url` 推，不依赖进程 cwd（DSH 里 cwd 是启动目录，不是会话工作区）。
 * - 引擎目录默认取**包内 `engine/`**，可被 Config.engineDir 覆盖。
 * - 自检只做「文件在不在 + 配置合不合理」，不跑引擎（跑引擎是各工具的事）。
 * - 会写盘的只有 `ensureDir` / `adoptRunDir` 两个，且都要求调用方给绝对路径。
 *
 * @module
 */
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** 包根目录（`src/lib/paths.js` → 上两级）。 */
export const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

/** 引擎里被本插件直接调用的文件（相对引擎目录）。 */
export const ENGINE_FILES = Object.freeze({
  manager: 'manager.py',
  gate: 'dag-gate.mjs',
  runner: 'runner.py',
  workflow: 'dag.workflow.mjs',
  rules: 'graph-rules.md',
  schema: 'manager.schema.json',
});

/**
 * `fixture` 引擎的回归夹具，**相对引擎根**（与 `engine/runner.py` 的
 * `ROOT / "tasks" / "DAG-2026-11-06" / ...` 同一条路径，见 runner.py:100）。
 * 夹具整目录随附在仓库里，保持原相对路径 → **引擎零改动**就能跑。
 */
const FIXTURES_REL = path.join('tasks', 'DAG-2026-11-06', 'fixtures');
const FIXTURE_WORKER_REL = path.join(FIXTURES_REL, 'fake-worker', 'worker.py');
/** 默认场景：随附的 `scenarios/honest`（3 节点 / 3 波 / result PASS）。 */
const DEFAULT_FIXTURE_SCENARIO = 'honest';

/** 运行产物目录名（Config.outDirName 的默认值）。 */
export const DEFAULT_OUT_DIR_NAME = '.ai-team';

/** 裁决原文返回给模型前的长度上限（SPEC §3.2：截断到 20000 字符）。 */
export const MAX_RAW_CHARS = 20000;

/** 引擎目录：Config.engineDir 优先，否则包内 `engine/`。 */
export function engineDirOf(config = {}) {
  const configured = typeof config.engineDir === 'string' ? config.engineDir.trim() : '';
  return configured ? path.resolve(configured) : path.join(PACKAGE_ROOT, 'engine');
}

/** 引擎根（引擎目录的父目录）—— 引擎把 `runs/` 写在这里（`ROOT = HERE.parent`）。 */
export function engineRootOf(config = {}) {
  return path.dirname(engineDirOf(config));
}

/** 取引擎里某个文件的绝对路径。 */
export function engineFileOf(config, key) {
  const file = ENGINE_FILES[key];
  if (!file) throw new Error(`未知的引擎文件键：${key}（可选：${Object.keys(ENGINE_FILES).join(' / ')}）`);
  return path.join(engineDirOf(config), file);
}

/** 包内模板目录（由 `templates/` 提供，`ai_team_init` 铺它）。 */
export function templatesDirOf() {
  return path.join(PACKAGE_ROOT, 'templates');
}

/**
 * 环境变量名合法性：`openaiApiKeyEnv` **只能是变量名**，绝不接受 key 字面量。
 * 用命名规则硬拦（而不是去猜「这串像不像 key」）：key 里几乎必然含 `-`/`_`+小写混排，环境变量名不允许。
 */
export function assertEnvName(name) {
  const value = String(name ?? '').trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(value)) {
    throw new Error(
      `openaiApiKeyEnv 必须是环境变量名（只含字母/数字/下划线，且不以数字开头），实际是 ${JSON.stringify(
        value.slice(0, 40),
      )}。本插件只从环境变量读 key，不接受 key 字面量。`,
    );
  }
  return value;
}

/**
 * 引擎自检：文件齐不齐、配置合不合理。**不跑任何引擎**。
 *
 * @param {object} config 插件 Config（已合并默认值）。
 * @returns {{ ok: boolean, engineDir: string, engineRoot: string, missing: string[], problems: string[] }}
 */
export function selfCheck(config = {}) {
  const engineDir = engineDirOf(config);
  const problems = [];
  const missing = [];

  if (!existsSync(engineDir)) {
    problems.push(`引擎目录不存在：${engineDir}（检查 Config.engineDir，或确认安装包带 engine/）`);
  } else {
    for (const [key, file] of Object.entries(ENGINE_FILES)) {
      const full = path.join(engineDir, file);
      if (!existsSync(full)) missing.push(`${key}: ${file}`);
    }
    if (missing.length > 0) problems.push(`引擎文件缺失（${engineDir}）：${missing.join('、')}`);
  }

  // pythonPath / nodePath 只是「要调哪个可执行文件」，这里只判非空 —— 起不起得来由真跑暴露
  if (!String(config.pythonPath ?? '').trim()) problems.push('Config.pythonPath 不能为空');
  if (!String(config.nodePath ?? '').trim()) problems.push('Config.nodePath 不能为空');

  try {
    assertEnvName(config.openaiApiKeyEnv ?? 'OPENAI_API_KEY');
  } catch (error) {
    problems.push(error.message);
  }

  return {
    ok: problems.length === 0,
    engineDir,
    engineRoot: path.dirname(engineDir),
    missing,
    problems,
  };
}

/**
 * 随附的场景名（`scenarios/*`，仓库带 40 个）。只读**一层**目录名，用于报错时告诉用户有哪些可挑。
 *
 * ⚠️ 本文件里所有夹具访问都是 `existsSync` / `readdirSync` 的**定点访问，不做递归遍历** ——
 * 所以不会被仓库根的 junction（本机为了跑自检把 DSH 的 node_modules 链进来过）拖住。
 * 会跟进 junction 的是 `engine/runner.py` 的 `snapshot()`，那条已转 docs/PORTING.md。
 *
 * @param {object} config 插件 Config。
 * @returns {string[]} 场景名（排序后）；目录不在则空数组。
 */
export function listFixtureScenarios(config = {}) {
  const dir = path.join(engineRootOf(config), FIXTURES_REL, 'scenarios');
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return [];
  return readdirSync(dir)
    .filter((name) => existsSync(path.join(dir, name, 'spec.json')))
    .sort();
}

/**
 * `fixture` 引擎的前置检查 —— **检查夹具真在不在**，不是「假定它跑不起来」。
 *
 * `engine/runner.py` 的 fixture 分支要两样东西：
 * 1. 假员工 `tasks/DAG-2026-11-06/fixtures/fake-worker/worker.py`（相对**引擎根**，runner.py:100）
 * 2. `--fixture-dir` 指向一个含 `spec.json` 的场景目录（每个场景一份）
 *
 * 两样都随附在本仓库里（夹具整目录原样搬来，保持原相对路径 → 引擎零改动）。
 * 所以这里的职责只是：**在就正常跑，缺什么说什么**，并附一条可复制的等价命令。
 *
 * 场景目录的选取顺序（SPEC §3.5）：
 * 1. 显式 `fixtureDir` 参数 —— 用户点名要哪个场景
 * 2. `<cwd>/<outDirName>/fixtures/spec.json` —— 用户自备场景
 * 3. `<引擎根>/tasks/.../fixtures/scenarios/honest/spec.json` —— 随附的默认场景
 *
 * @param {object} config 插件 Config。
 * @param {string} cwd 会话工作目录。
 * @param {string} [fixtureDir] 显式指定的场景目录（相对路径按 cwd 解析）。
 * @returns {{ ok: boolean, worker: string, fixtureDir: string, why: string, repro: string, explicit: boolean }}
 */
export function fixtureCheck(config = {}, cwd = process.cwd(), fixtureDir) {
  const root = engineRootOf(config);
  const fixturesRoot = path.join(root, FIXTURES_REL);
  const worker = path.join(root, FIXTURE_WORKER_REL);
  const explicitRaw = typeof fixtureDir === 'string' ? fixtureDir.trim() : '';
  const explicit = explicitRaw ? resolveInCwd(explicitRaw, cwd) : '';
  const candidates = explicit
    ? [explicit]
    : [
        path.join(workspaceDir(cwd, config.outDirName), 'fixtures'),
        path.join(fixturesRoot, 'scenarios', DEFAULT_FIXTURE_SCENARIO),
      ];
  const chosen = candidates.find((dir) => existsSync(path.join(dir, 'spec.json'))) ?? '';

  const problems = [];
  if (!existsSync(worker)) problems.push(`假员工不在：${worker}`);
  if (!chosen) {
    problems.push(
      explicit
        ? `fixtureDir 指向的目录里没有 spec.json：${explicit}`
        : `找不到含 spec.json 的场景目录（试过：${candidates.join(' ； ')}）`,
    );
  }

  const scenarios = listFixtureScenarios(config);
  const scenarioHint =
    scenarios.length > 0
      ? `随附场景（${scenarios.length} 个）可选：${scenarios.join(' / ')}。` +
        `用 fixtureDir 指向 ${path.join(fixturesRoot, 'scenarios', '<名字>')} 即可切换。`
      : '';

  // 可复制的等价命令：从引擎根跑，路径写成相对形式（与文档一致）
  const reproScenario = explicit ? path.relative(path.join(root), explicit) || explicit : path.posix.join(
    ...FIXTURES_REL.split(path.sep),
    'scenarios',
    DEFAULT_FIXTURE_SCENARIO,
  );
  const repro =
    `python engine/runner.py ${path.posix.join(...FIXTURES_REL.split(path.sep), 'graphs', 'g3.json')} ` +
    `--engine fixture --fixture-dir ${reproScenario.split(path.sep).join('/')}`;

  const why =
    problems.length === 0
      ? ''
      : `fixture 夹具检查没过：${problems.join('；')}。` +
        "要跑真模型请用 engine='headless'（会花额度）或 engine='codex'；" +
        `或缺的夹具补回来（随附路径：${fixturesRoot}）。${scenarioHint}等价手跑命令：${repro}`;

  return { ok: problems.length === 0, worker, fixtureDir: chosen, why, repro, explicit: Boolean(explicit) };
}

/**
 * 读写会话工作目录：
 * 优先 `ctx.get('workingDirectory')`（DSH 记录的会话目录，`inject` 里不声明也能取），
 * 取不到才退回进程 cwd —— **不静默**，退回分支会在工具输出的 `outDir` 上体现。
 *
 * @param {object} ctx 插件上下文。
 * @param {{ agent?: unknown, signal?: AbortSignal }} exec 工具执行上下文。
 * @returns {Promise<string>} 绝对目录。
 */
export async function cwdOf(ctx, exec) {
  const service = typeof ctx?.get === 'function' ? ctx.get('workingDirectory') : undefined;
  if (service && typeof service.ensure === 'function' && exec?.agent !== undefined) {
    return await service.ensure(exec.agent, exec.signal);
  }
  return process.cwd();
}

/** `<cwd>/<outDirName>`：本插件的运行产物根（默认 `.ai-team`）。 */
export function workspaceDir(cwd, outDirName) {
  const name = String(outDirName ?? '').trim() || DEFAULT_OUT_DIR_NAME;
  return path.join(path.resolve(cwd), name);
}

/** 建目录（递归）并返回它。 */
export function ensureDir(dir) {
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** 相对路径按 cwd 解析成绝对路径。 */
export function resolveInCwd(target, cwd) {
  const raw = String(target ?? '').trim();
  if (!raw) throw new Error('路径不能为空');
  return path.isAbsolute(raw) ? path.normalize(raw) : path.resolve(cwd, raw);
}

/** 截断文本到 `max` 字符（超了带省略标记）。 */
export function clip(text, max = MAX_RAW_CHARS) {
  const s = String(text ?? '');
  if (s.length <= max) return s;
  return `${s.slice(0, max)}\n…[已截断：原文 ${s.length} 字符，只保留前 ${max}]`;
}

/**
 * 解析 `brief` 参数：`.md` 结尾按路径读文件，其余当正文。
 *
 * @param {string} value 模型给的 `brief`。
 * @param {string} cwd 会话工作目录。
 * @returns {{ path: string|null, text: string }}
 */
export function resolveBriefInput(value, cwd) {
  const raw = typeof value === 'string' ? value : '';
  if (raw.trim() === '') throw new Error('brief 不能为空');
  if (!/\.md$/i.test(raw.trim())) return { path: null, text: raw };
  const abs = resolveInCwd(raw, cwd);
  if (!existsSync(abs) || !statSync(abs).isFile()) {
    throw new Error(`brief 以 .md 结尾，但文件不存在：${abs}`);
  }
  return { path: abs, text: readFileSync(abs, 'utf8') };
}

/** 生成 run id：`<prefix>-YYYYMMDD-HHMMSS`。 */
export function makeRunId(prefix = 'run', now = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  const stamp =
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `${prefix}-${stamp}`;
}

/**
 * 校验 run id：只允许 `[A-Za-z0-9._-]`，禁止 `.` / `..` 和空串。
 * 它是**目录名**，也是引擎的 `--run-id`，必须挡住 `../` 这类穿越（路径是要被删除/移动的）。
 */
export function safeRunId(value) {
  const id = String(value ?? '').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(id) || id === '.' || id === '..') {
    throw new Error(`runId 非法：${JSON.stringify(id.slice(0, 64))}（只允许字母、数字、点、下划线、连字符，且必须以字母或数字开头）`);
  }
  return id;
}

/**
 * 把引擎写在自己根下的 `runs/<runId>` **搬到**目标目录。
 *
 * 为什么要搬：`engine/manager.py` 与 `engine/runner.py` 都把 `ROOT = HERE.parent` 写死成
 * `<引擎根>`，产物因此落在安装目录里（npm 包里就是 `node_modules/` 里），既不可见也不该留。
 * 这里在**调用成功后**把它整体搬进 `<cwd>/.ai-team/runs/<runId>`，然后把源目录删掉。
 *
 * 安全边界（这个函数会 `rmSync`，所以每一条都必须成立）：
 * - 源路径必须**正好**是 `<engineRoot>/<subdir>/<runId>`，`runId` 已过 `safeRunId`；
 * - 源必须是目录，目标必须与之不同；
 * - 只删我们自己刚造出来的那一层 `runs/<runId>`。
 *
 * @param {{ engineRoot: string, runId: string, destDir: string, subdir?: string }} options
 * @returns {{ dir: string, moved: boolean }} 产物最终所在目录。
 */
export function adoptRunDir({ engineRoot, runId, destDir, subdir = 'runs' }) {
  const safeId = safeRunId(runId);
  const sourceRoot = path.resolve(engineRoot, subdir);
  const source = path.join(sourceRoot, safeId);
  const dest = path.resolve(destDir);

  if (path.dirname(source) !== sourceRoot) {
    throw new Error(`拒绝搬运：源路径不在 ${sourceRoot} 下（${source}）`);
  }
  if (dest === source) return { dir: dest, moved: false };
  if (!existsSync(source)) {
    // 引擎没落盘（例如 dry-run）—— 不算错，交给调用方判
    return { dir: dest, moved: false };
  }
  if (!statSync(source).isDirectory()) {
    throw new Error(`拒绝搬运：源不是目录（${source}）`);
  }

  ensureDir(dest);
  for (const entry of readdirSync(source)) {
    cpSync(path.join(source, entry), path.join(dest, entry), { recursive: true, force: true });
  }
  rmSync(source, { recursive: true, force: true });
  return { dir: dest, moved: true };
}
