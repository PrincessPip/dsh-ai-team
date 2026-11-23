/**
 * `ai_team_run` —— 执行图（SPEC §3.5）。调用 `engine/runner.py`。
 *
 * 安全默认是 `engine='fixture'`（不花钱、不写主树）—— 它是**真能跑的**：
 * `runner.py` 的 fixture 分支要假员工 `tasks/DAG-2026-11-06/fixtures/fake-worker/worker.py`
 * 与一个含 `spec.json` 的场景目录，两者都随附在本仓库（保持原相对路径，引擎零改动）。
 * 所以这里只做一件事：**检查夹具真在不在**，缺什么说什么并附一条可复制的等价命令
 * （见 `paths.fixtureCheck`）—— 而不是让 runner 花几分钟把每个节点跑成 `python: can't open file`。
 *
 * 场景（`fixtureDir`，SPEC §3.5）：仓库随带 40 个回归场景（honest / overclaim / wash / retry /
 * timeout / parallel-* / evidence-tamper …）。选取顺序：
 * 显式 `fixtureDir` > `<cwd>/.ai-team/fixtures` > 随附 `scenarios/honest`。
 * 非 `fixture` 引擎却传了 `fixtureDir` → **明确报错**（静默忽略会让人误以为场景切换生效了）。
 *
 * 退出码语义（`engine/runner.py:2849`）：0 = result pass／1 = result fail／2 = die(2)（图不合法、
 * 环境不满足、运行目录已存在等）。0/1 都算跑完了，2 才是工具失败。
 *
 * ⚠️ `result='pass'` 只代表**流程**跑通：产物对不对要另跑 `verify-product`（原工程有，本仓库未随附）。
 *
 * @module
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { jsonOutput } from '../lib/output.js';
import { runProcess } from '../lib/proc.js';
import {
  adoptRunDir,
  clip,
  cwdOf,
  engineFileOf,
  engineRootOf,
  ensureDir,
  fixtureCheck,
  makeRunId,
  resolveInCwd,
  safeRunId,
  selfCheck,
  workspaceDir,
} from '../lib/paths.js';

/** 可选的执行引擎（与 runner.py 的 `--engine` choices 一致）。 */
const ENGINES = Object.freeze(['headless', 'fixture', 'codex']);

/** 引擎输出贴进错误信息的长度上限。 */
const TAIL = 3000;

/** 读 JSON；坏文件返回 `null`。 */
function readJsonSafe(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return null;
  }
}

/**
 * 单节点耗时（秒）。`RUN.json.nodes[]` 里没有这个字段，所以自己从证据包推：
 * 优先 `NODES/<id>.json` 的 `started_at`（epoch 秒）+ `finished_iso`（本地 ISO）；
 * 拿不到就查 `RUN.json.parallel.groups[].rounds[].spans`；都没有记 0 —— **不编一个数**。
 */
function secondsOf(nodeId, nodesDir, runJson) {
  const rec = readJsonSafe(path.join(nodesDir, `${nodeId}.json`));
  const started = typeof rec?.started_at === 'number' ? rec.started_at : null;
  const finished = typeof rec?.finished_at === 'number' ? rec.finished_at : null;
  if (started !== null && finished !== null) return Math.max(0, Math.round((finished - started) * 1000) / 1000);
  if (started !== null && typeof rec?.finished_iso === 'string') {
    const end = Date.parse(rec.finished_iso);
    if (Number.isFinite(end)) return Math.max(0, Math.round((end - started * 1000) / 1000) / 1000);
  }
  const groups = Array.isArray(runJson?.parallel?.groups) ? runJson.parallel.groups : [];
  for (const group of groups) {
    for (const round of Array.isArray(group?.rounds) ? group.rounds : []) {
      const span = round?.spans?.[nodeId];
      if (span && typeof span.started_at === 'number' && typeof span.finished_at === 'number') {
        return Math.max(0, Math.round((span.finished_at - span.started_at) * 1000) / 1000);
      }
    }
  }
  return 0;
}

/** 把 `RUN.json.nodes`（对象）摊成 SPEC §3.5 要的数组。 */
function collectNodes(nodesDir, runJson, runDir) {
  const raw = runJson?.nodes;
  const ids =
    raw && typeof raw === 'object' && !Array.isArray(raw)
      ? Object.keys(raw)
      : existsSync(nodesDir)
        ? readdirSync(nodesDir)
            .filter((name) => name.endsWith('.json') && !/\.a\d+\.json$/.test(name))
            .map((name) => name.slice(0, -'.json'.length))
        : [];
  return ids.map((id) => {
    const record = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw[id] : null;
    const status = typeof record?.status === 'string' && record.status ? record.status : 'unknown';
    return { id, status, seconds: secondsOf(id, nodesDir, runJson) };
  });
}

/**
 * @param {object} ctx 插件上下文。
 * @param {object} config 插件 Config。
 * @returns {object} 工具定义。
 */
export function createRunTool(ctx, config) {
  return defineTool({
    name: 'ai_team_run',
    description:
      '按 GRAPH.json 执行 DAG（调 engine/runner.py；runner 自己会先过一遍 Gate，被拒收就不跑）。' +
      "engine 默认 'fixture'：假员工 + 场景 spec，不花钱、不写主树，真能跑通全链。" +
      '仓库随带 40 个回归场景（honest / overclaim / wash / retry / timeout / parallel-* / evidence-tamper …），' +
      '用 fixtureDir 指向其中一个即可切换；不传则依次回落到 <工作目录>/.ai-team/fixtures、随附的 scenarios/honest。' +
      "'headless' 真调模型（会花额度），'codex' 走 codex CLI。" +
      'result=pass 只代表**流程**跑通：产物对不对另需 verify-product 复核（本仓库未随附）。',
    parameters: {
      graph: { type: 'string', required: true, description: 'GRAPH.json 路径（相对路径按工作目录解析）' },
      engine: {
        type: 'string',
        description: "执行引擎：fixture（默认，安全）/ headless（真调模型）/ codex",
        enum: [...ENGINES],
      },
      runId: { type: 'string', description: '运行 id，不填按时间生成' },
      fixtureDir: {
        type: 'string',
        description:
          "仅 engine='fixture' 时生效：场景目录（含 spec.json）。相对路径按工作目录解析；" +
          '不传则回落 <工作目录>/.ai-team/fixtures，再回落随附 scenarios/honest',
      },
    },
    output: jsonOutput({
      type: 'object',
      additionalProperties: false,
      properties: {
        runId: { type: 'string', required: true },
        result: { type: 'string', required: true, enum: ['pass', 'fail'] },
        nodes: {
          type: 'array',
          required: true,
          items: {
            type: 'object',
            additionalProperties: false,
            properties: {
              id: { type: 'string', required: true },
              status: { type: 'string', required: true },
              seconds: { type: 'number', required: true },
            },
          },
        },
        runDir: { type: 'string', required: true },
      },
    }),
    timeoutMs: config.timeoutMs,
    async execute(args, exec) {
      const check = selfCheck(config);
      if (!check.ok) throw new Error(`引擎自检没过：${check.problems.join('；')}`);

      const engine = String(args.engine ?? 'fixture')
        .trim()
        .toLowerCase();
      if (!ENGINES.includes(engine)) {
        throw new Error(`未知 engine "${String(args.engine)}"；可选：${ENGINES.join(' / ')}`);
      }

      const cwd = await cwdOf(ctx, exec);
      const graphPath = resolveInCwd(args.graph, cwd);
      if (!existsSync(graphPath)) throw new Error(`GRAPH.json 不存在：${graphPath}`);

      // fixtureDir 只在 fixture 引擎上有意义。给了又不用 → **明确报错**，不静默吞
      // （静默忽略会让人以为「场景切换生效了」，而实际跑的是 honest）。
      const requestedFixtureDir = typeof args.fixtureDir === 'string' ? args.fixtureDir.trim() : '';
      if (requestedFixtureDir && engine !== 'fixture') {
        throw new Error(
          `fixtureDir 只在 engine='fixture' 时生效，当前 engine='${engine}'。` +
            '要么删掉 fixtureDir，要么把 engine 改成 fixture。',
        );
      }

      // fixture 的前置（假员工 + spec.json）先问清楚，别等 runner 把每个节点跑成 python 报错
      let fixtureDir = '';
      if (engine === 'fixture') {
        const fixture = fixtureCheck(config, cwd, requestedFixtureDir);
        if (!fixture.ok) throw new Error(fixture.why);
        fixtureDir = fixture.fixtureDir;
      }

      const runId = safeRunId(args.runId ? args.runId : makeRunId('run'));
      const outDir = path.join(workspaceDir(cwd, config.outDirName), 'runs', runId);
      ensureDir(outDir);

      const runnerPath = engineFileOf(config, 'runner');
      const argv = [runnerPath, graphPath, '--engine', engine, '--run-id', runId];
      if (fixtureDir) argv.push('--fixture-dir', fixtureDir);

      const result = await runProcess(config.pythonPath, argv, {
        cwd,
        timeoutMs: config.timeoutMs,
        signal: exec.signal,
      });
      const log = clip(`${result.stdout}\n${result.stderr}`.trim(), TAIL);

      if (result.timedOut) {
        throw new Error(`执行超时被强制结束（已 kill 进程树，${config.timeoutMs} ms）。输出尾部：\n${log}`);
      }
      if (result.code === 2) {
        throw new Error(`runner 拒绝执行（exit 2）：\n${clip(result.stdout || result.stderr, TAIL) || '（无输出）'}`);
      }
      if (result.code !== 0 && result.code !== 1) {
        throw new Error(`runner 异常退出（exit ${result.code}）：\n${clip(result.stdout || result.stderr, TAIL)}`);
      }

      const adopted = adoptRunDir({ engineRoot: engineRootOf(config), runId, destDir: outDir });
      const runJson = readJsonSafe(path.join(adopted.dir, 'RUN.json'));
      if (!runJson) {
        throw new Error(
          `runner 退出码 ${result.code}，但没有可读的 RUN.json（找过 ${path.join(adopted.dir, 'RUN.json')}）。` +
            `输出尾部：\n${log}`,
        );
      }

      const declared = runJson.result === 'pass' ? 'pass' : 'fail';
      // 以**证据文件**为准：RUN.json 说 fail 就是 fail，不看退出码说话
      if (declared === 'fail' && result.code === 0) {
        runJson.runnerExitCode = result.code;
      }

      return {
        runId,
        result: declared,
        nodes: collectNodes(path.join(adopted.dir, 'NODES'), runJson, adopted.dir),
        runDir: adopted.dir,
      };
    },
  });
}
