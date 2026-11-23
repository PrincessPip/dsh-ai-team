/**
 * `ai_team_plan` —— 需求 → DAG 图（SPEC §3.3）。调用 `engine/manager.py`。
 *
 * 引擎的落盘位置是写死的：`manager.py` 里 `ROOT = HERE.parent`，产物永远落在
 * `<引擎根>/runs/<run_id>/`（npm 包里就是安装目录）。所以这里调用成功后把整包**搬**到
 * `<cwd>/.ai-team/runs/<runId>/`，再删掉引擎那份 —— 见 `paths.js` 的 `adoptRunDir()`。
 *
 * 「Gate 拒收」不是工具失败：图会照常落盘（`GRAPH.json` + `GATE.json` 里有 errs），
 * 这里把结论写进 `summary` 并如常返回；要不要改图由调用方按 `ai_team_gate` 的结论决定。
 *
 * @module
 */
import { existsSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import os from 'node:os';
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
  makeRunId,
  resolveBriefInput,
  resolveInCwd,
  safeRunId,
  selfCheck,
  workspaceDir,
} from '../lib/paths.js';

/** 引擎输出贴进 `summary` 的长度上限。 */
const TAIL = 4000;
/** 拒收原因最多列几条（全列会把 summary 撑爆，完整清单在 GATE.json 里）。 */
const MAX_LISTED_ERRORS = 8;

/** 读 JSON；读不到/不合法返回 `null`（引擎偶尔会留半截文件）。 */
function readJsonSafe(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return null;
  }
}

/** 组 `summary` 文案：人读一句话，细节指到文件。 */
function buildSummary({ runId, graphJson, gateJson, outDir, executed }) {
  const nodes = Array.isArray(graphJson?.nodes) ? graphJson.nodes.length : 0;
  const lines = [];
  const gateOk = gateJson ? Boolean(gateJson.ok) : null;

  if (gateOk === true) {
    lines.push(
      `Gate OK · ${nodes} 节点 · ${gateJson.waveCount ?? '?'} 波 · 关键路径 ${gateJson.criticalLen ?? '?'} 节点 · ` +
        `${gateJson.warnCount ?? 0} warn`,
    );
  } else if (gateOk === false) {
    const errs = Array.isArray(gateJson.errs) ? gateJson.errs : [];
    lines.push(`Gate REJECTED（${errs.length} 条 error）—— 图已落盘，但**不许拿去跑**，先改图再 ai_team_gate：`);
    for (const err of errs.slice(0, MAX_LISTED_ERRORS)) {
      lines.push(`  - [${err?.code ?? '?'}] ${err?.msg ?? ''}`);
    }
    if (errs.length > MAX_LISTED_ERRORS) lines.push(`  …另有 ${errs.length - MAX_LISTED_ERRORS} 条，见 GATE.json`);
  } else {
    lines.push(`图已产出（${nodes} 节点），但没有 GATE.json —— 用 ai_team_gate 判一遍再跑`);
  }

  if (Array.isArray(gateJson?.waves) && gateJson.waves.length > 0) {
    const waves = gateJson.waves.map((wave, index) => `wave${index}: ${(wave || []).join(' · ')}`).join('；');
    lines.push(`波次：${waves}`);
  }
  lines.push(`产物：${outDir}${executed ? `（runId=${runId}）` : ''}`);

  return lines.join('\n');
}

/**
 * @param {object} ctx 插件上下文。
 * @param {object} config 插件 Config。
 * @returns {object} 工具定义。
 */
export function createPlanTool(ctx, config) {
  return defineTool({
    name: 'ai_team_plan',
    description:
      '把需求简报变成一张 DAG 任务图（调 engine/manager.py，模型由后端订阅通道提供）。' +
      '产出 GRAPH.json + GATE.json + RUN.json；summary 会告诉你 Gate 是 OK 还是 REJECTED。' +
      'REJECTED 时图还在，但必须先改图再跑 —— 下一步应当调 ai_team_gate 复核。' +
      'dryRun=true 只检查环境与规则漂移（不调模型、不落盘）。',
    parameters: {
      brief: { type: 'string', required: true, description: '需求文本，或以 .md 结尾时按路径读取' },
      outDir: { type: 'string', description: '产物目录，默认 <工作目录>/<outDirName>/runs/<runId>' },
      model: { type: 'string', description: '转给经理层的模型名（留空用引擎默认）' },
      dryRun: { type: 'boolean', description: '只查环境与规则漂移，不调模型' },
    },
    output: jsonOutput({
      type: 'object',
      additionalProperties: false,
      properties: {
        graphPath: { type: 'string', required: true },
        runId: { type: 'string', required: true },
        nodeCount: { type: 'number', required: true },
        summary: { type: 'string', required: true },
      },
    }),
    timeoutMs: config.timeoutMs,
    async execute(args, exec) {
      const check = selfCheck(config);
      if (!check.ok) throw new Error(`引擎自检没过：${check.problems.join('；')}`);

      const cwd = await cwdOf(ctx, exec);
      const runId = safeRunId(makeRunId('plan'));
      const dryRun = args.dryRun === true;
      const outDir = dryRun
        ? null
        : ensureDir(
            args.outDir
              ? resolveInCwd(args.outDir, cwd)
              : path.join(workspaceDir(cwd, config.outDirName), 'runs', runId),
          );

      // brief 有两种来源：.md 文件（直接把它交给经理层）或正文（先落盘成 BRIEF.md）。
      // dry-run 不建产物目录 —— 它连图都不出，留个半截证据包只会误导（本工程的老教训）。
      const brief = resolveBriefInput(args.brief, cwd);
      let briefPath = brief.path;
      /** @type {string|null} dry-run 时的临时简报，跑完即删 */
      let tempBrief = null;
      if (!briefPath) {
        if (dryRun) {
          tempBrief = path.join(os.tmpdir(), `ai-team-${runId}-BRIEF.md`);
          writeFileSync(tempBrief, brief.text, 'utf8');
          briefPath = tempBrief;
        } else {
          briefPath = path.join(outDir, 'BRIEF.md');
          writeFileSync(briefPath, brief.text, 'utf8');
        }
      }

      const managerPath = engineFileOf(config, 'manager');
      const argv = [managerPath, briefPath, '--run-id', runId];
      const model = typeof args.model === 'string' ? args.model.trim() : '';
      if (model) argv.push('--model', model);
      if (dryRun) argv.push('--dry-run');

      let result;
      try {
        result = await runProcess(config.pythonPath, argv, {
          cwd,
          timeoutMs: config.timeoutMs,
          signal: exec.signal,
        });
      } finally {
        if (tempBrief) rmSync(tempBrief, { force: true });
      }
      const log = clip(`${result.stdout}\n${result.stderr}`.trim(), TAIL);

      if (result.timedOut) {
        throw new Error(`经理层超时被强制结束（已 kill 进程树，${config.timeoutMs} ms）。输出尾部：\n${log}`);
      }
      if (dryRun) {
        // dry-run 不落盘（manager.py 直接 return），所以 graphPath 为空、nodeCount 为 0
        if (result.code !== 0) {
          throw new Error(`经理层 dry-run 失败（exit ${result.code}）：\n${clip(result.stdout || result.stderr, TAIL)}`);
        }
        return {
          graphPath: '',
          runId,
          nodeCount: 0,
          summary: `dry-run：没有调模型、没有出图、没有产物目录。环境与规则漂移检查输出：\n${log}`,
        };
      }
      if (result.code === 2) {
        // 2 = 输入/环境不满足（die(2)），这次连图都没产出
        throw new Error(`经理层拒绝执行（exit 2）：\n${clip(result.stdout || result.stderr, TAIL) || '（无输出）'}`);
      }

      // 把引擎写在自己根下的 runs/<runId>/ 搬进用户看得见的目录
      const adopted = adoptRunDir({ engineRoot: engineRootOf(config), runId, destDir: outDir });
      const graphPath = path.join(adopted.dir, 'GRAPH.json');
      if (!existsSync(graphPath) || !statSync(graphPath).isFile()) {
        throw new Error(
          `经理层退出码 ${result.code}，但没有产出 GRAPH.json（找过 ${graphPath}）。输出尾部：\n` +
            clip(result.stdout || result.stderr, TAIL),
        );
      }

      const graphJson = readJsonSafe(graphPath);
      const gateJson = readJsonSafe(path.join(adopted.dir, 'GATE.json'));
      const nodeCount = Array.isArray(graphJson?.nodes) ? graphJson.nodes.length : 0;

      return {
        graphPath,
        runId,
        nodeCount,
        summary: buildSummary({ runId, graphJson, gateJson, outDir: adopted.dir, executed: true }),
      };
    },
  });
}
