/**
 * `ai_team_gate` —— 判图（SPEC §3.4）。调用 `engine/dag-gate.mjs`。
 *
 * 关键纪律：`ok=false` 时**不许继续跑**。这里不替模型做决定（不 throw），
 * 而是把结论原样返回，并在 description 里把「必须停」写清楚 —— 因为「图被拒收」是**正常结果**，
 * 不是工具失败；把它当异常抛反而会让模型看不到 errs 明细。
 *
 * 退出码语义（来自 `engine/dag-gate.mjs:14`）：
 *   0 = 通过（可能有 warn）／1 = 被 Gate 拒收／2 = 读不到或不是合法 JSON
 * 其中 2 是**调用方给错了图**，属于工具失败 → throw（带 fatal 原因）。
 *
 * @module
 */
import { existsSync } from 'node:fs';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { jsonOutput } from '../lib/output.js';
import { runProcess } from '../lib/proc.js';
import { clip, cwdOf, engineFileOf, resolveInCwd, selfCheck } from '../lib/paths.js';

/** Gate 输出尾部截断长度（错误信息用）。 */
const TAIL = 1200;

/** 解析 `--json` 的输出；JSON 前面可能混进别的行，所以从后往前试。 */
function parseGateJson(stdout) {
  const text = String(stdout ?? '').trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    /* 继续尝试逐行解析 */
  }
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (!lines[i].startsWith('{')) continue;
    try {
      return JSON.parse(lines[i]);
    } catch {
      /* 这一行不是 JSON，接着往前找 */
    }
  }
  return null;
}

/** 把 Gate 的 {code,msg} 数组收敛成稳定形状（缺字段也不崩）。 */
function normalizeEntries(list) {
  if (!Array.isArray(list)) return [];
  return list.map((item) => ({
    code: typeof item?.code === 'string' ? item.code : String(item?.code ?? 'UNKNOWN'),
    msg: typeof item?.msg === 'string' ? item.msg : String(item?.msg ?? ''),
  }));
}

/**
 * @param {object} ctx 插件上下文。
 * @param {object} config 插件 Config。
 * @returns {object} 工具定义。
 */
export function createGateTool(ctx, config) {
  return defineTool({
    name: 'ai_team_gate',
    description:
      '用引擎自带的 Gate 判一张 GRAPH.json（四十条硬规则 + 波次/关键路径）。' +
      '返回 ok=false 就是**被拒收**：这一步必须停，先按 errs 里的 code 改图，不许继续 ai_team_run。' +
      'ok=true 只代表「图合法」，不代表需求被满足。',
    parameters: {
      graph: { type: 'string', required: true, description: 'GRAPH.json 路径（相对路径按工作目录解析）' },
    },
    output: jsonOutput({
      type: 'object',
      additionalProperties: false,
      properties: {
        ok: { type: 'boolean', required: true },
        errs: {
          type: 'array',
          required: true,
          items: {
            type: 'object',
            additionalProperties: false,
            properties: {
              code: { type: 'string', required: true },
              msg: { type: 'string', required: true },
            },
          },
        },
        warns: {
          type: 'array',
          required: true,
          items: {
            type: 'object',
            additionalProperties: false,
            properties: {
              code: { type: 'string', required: true },
              msg: { type: 'string', required: true },
            },
          },
        },
        codes: { type: 'array', required: true, items: { type: 'string' } },
      },
    }),
    timeoutMs: Math.min(config.timeoutMs, 600000),
    async execute(args, exec) {
      const check = selfCheck(config);
      if (!check.ok) throw new Error(`引擎自检没过：${check.problems.join('；')}`);

      const cwd = await cwdOf(ctx, exec);
      const graphPath = resolveInCwd(args.graph, cwd);
      if (!existsSync(graphPath)) throw new Error(`GRAPH.json 不存在：${graphPath}`);

      const gatePath = engineFileOf(config, 'gate');
      // Gate 是纯计算（读源码 + 解释一张图），给 10 分钟足够；它不该耗时
      const result = await runProcess(config.nodePath, [gatePath, graphPath, '--json'], {
        cwd,
        timeoutMs: Math.min(config.timeoutMs, 600000),
        signal: exec.signal,
      });

      const parsed = parseGateJson(result.stdout);

      if (result.timedOut) {
        throw new Error(`Gate 超时被强制结束（已 kill 进程树）：${gatePath}`);
      }
      if (result.code === 2) {
        const fatal = typeof parsed?.fatal === 'string' ? parsed.fatal : '';
        throw new Error(
          `Gate 读不到这张图（exit 2）：${fatal || clip(result.stderr || result.stdout, TAIL) || '（无输出）'}`,
        );
      }
      if (result.code !== 0 && result.code !== 1) {
        throw new Error(
          `Gate 异常退出（exit ${result.code}）：${clip(result.stderr || result.stdout, TAIL) || '（无输出）'}`,
        );
      }
      if (!parsed) {
        throw new Error(`Gate 的 --json 输出不是合法 JSON：${clip(result.stdout, TAIL) || '（无输出）'}`);
      }

      const errs = normalizeEntries(parsed.errs);
      const warns = normalizeEntries(parsed.warns);
      const codes = [...new Set([...errs, ...warns].map((item) => item.code))];

      return { ok: Boolean(parsed.ok), errs, warns, codes };
    },
  });
}
