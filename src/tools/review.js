/**
 * `ai_team_review` —— 核心工具：送独立验收官，产出三态裁决（SPEC §3.2）。
 *
 * 这条链上每一步都在防「自证通过」：
 * 1. prompt 里写死裁决纪律（三态、证据强制、只读纪律）；
 * 2. 「只读」**不是沙箱隔离**：codex 沙箱档位由 `Config.codexSandbox` 决定（默认 workspace-write ——
 *    Windows 上 `-s read-only` 会禁掉一切命令，见 `engine/runner.py:1436-1440`），
 *    真正的执法手段是**证据落盘 + 事后可核**，所以返回值钉死 `enforcement: 'snapshot_only'`；
 * 3. 裁决由 `verdict.js` 解析 —— **只认结构化字段**，散文里的"通过"不算；
 * 4. 解析/归一化不过关一律降级 `escalate`，最后再过一次 `assertVerdict` 才返回；
 * 5. 原文、prompt、元信息、裁决四份证据全部落盘（`outDir`），裁决可事后复核。
 *
 * @module
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { jsonOutput } from '../lib/output.js';
import { assertVerdict, normalizeVerdict, parseVerdict, NO_VERDICT_BLOCKING } from '../lib/verdict.js';
import {
  MAX_RAW_CHARS,
  clip,
  cwdOf,
  ensureDir,
  makeRunId,
  resolveBriefInput,
  templatesDirOf,
  workspaceDir,
} from '../lib/paths.js';
import { selectBackend } from '../adapters/index.js';

/**
 * 只读执法的诚实档位。**别改成 'sandbox' 之类** —— 那会变成"声称隔离"。
 * 实际 codex 沙箱档位记在 `outDir/META.json` 的 `meta.sandbox` 里，两者不可混为一谈。
 */
const ENFORCEMENT = 'snapshot_only';

/** 团队模板里有的话，把格式要求贴进 prompt 的上限（不要把整份模板灌进去）。 */
const TEMPLATE_BUDGET = 8000;

/** 轮次合法性：正整数（与 `engine/review-codex.py` 的口径一致）。 */
const ROUND_PATTERN = /^[1-9][0-9]{0,8}$/;

/** 组装验收 prompt。纯函数：同样的输入永远得到同样的 prompt（便于 dry-run 与复核）。 */
function buildReviewPrompt({ briefText, round, backend, cwd }) {
  const sections = [];

  sections.push(
    [
      '你是**独立验收官**。你审的是别人（工人）的交付，不是你自己写的代码。',
      '',
      '## 裁决纪律（硬规则，违反即视为不合规）',
      '1. 只允许三态：`pass` / `rework` / `escalate`。不许写「基本可以」「大致没问题」。',
      '2. 每条 `pass` 必须有证据：`file:line` 或命令 + 输出摘要。引不出证据的结论 → 该条降为 `rework` 的 blocking。',
      '3. 证据强度自评 `E0`–`E3`：E0 = 只有结论（什么都不支撑）；E1 = 单点观察；',
      '   E2 = 可复现或双维交叉；E3 = 双维交叉 + 反证。**没声明按 E0 处理**。',
      '4. 你是**只读**的：不要创建/修改/删除任何文件，不要改配置。只读命令（读文件、grep、ls）可以跑。',
      '   （注：这是纪律约定，不是沙箱隔离 —— 你的产出会连证据包一起落盘，事后可核。）',
      '5. `verdict=pass` 时 `blocking` 必须为空；有 blocking 就必须是 `rework`（自相矛盾会被降级）。',
      '6. 最后必须给出一个 ```json 代码块，字段如下（只认结构化字段，散文不算裁决）：',
      '',
      '```json',
      '{',
      '  "verdict": "pass | rework | escalate",',
      '  "confidence": 0.0,',
      '  "blocking": ["[B1] 问题是什么 ｜ 理由：为什么阻断 ｜ 证据：file:line 或命令输出"],',
      '  "findings": [',
      '    { "id": "C1", "claim": "这一条审的是什么", "evidence": "file:line + 锚串，或命令 + 输出摘要", "type": "E2" }',
      '  ]',
      '}',
      '```',
      '',
      '`escalate` 用于「我看不到判不了」或「需要人来决定」——它比猜一个 pass 更负责。',
    ].join('\n'),
  );

  sections.push(
    [
      '## 本次验收现场',
      `- 工作目录：${cwd}`,
      `- 轮次：第 ${round} 轮`,
      `- 后端：${backend}`,
    ].join('\n'),
  );

  if (backend === 'openai') {
    sections.push(
      [
        '## 限制声明（务必据实判断）',
        '本后端是**纯文本模型**：你看不到工作区文件，也跑不了命令。',
        '所以你能判的只有「任务书里的叙述是否自洽、有没有明显缺口」；',
        '任何需要读文件或跑命令才能定的结论，请如实标 `E0` 或直接 `escalate`，不要假装验过。',
      ].join('\n'),
    );
  }

  // 团队模板（若已随包提供）——它规定的是输出格式，不是本次任务内容
  const templatePath = path.join(templatesDirOf(), 'REVIEW-BRIEF.md');
  if (existsSync(templatePath)) {
    try {
      const template = readFileSync(templatePath, 'utf8');
      sections.push(`## 团队模板（REVIEW-BRIEF.md，只作格式参考）\n\n${clip(template, TEMPLATE_BUDGET)}`);
    } catch {
      /* 模板读不到不影响验收：prompt 里的纪律已经写全 */
    }
  }

  sections.push(`## 本次任务书\n\n${briefText}`);

  return sections.join('\n\n---\n\n');
}

/** 把后端相关的 Config 摊成 adapter 的参数（各后端只取自己要的）。 */
function backendOptions(config, backend) {
  if (backend === 'codex') {
    return { codexPath: config.codexPath, sandbox: config.codexSandbox };
  }
  if (backend === 'openai') return { baseUrl: config.openaiBaseUrl, apiKeyEnv: config.openaiApiKeyEnv };
  return {};
}

/**
 * @param {object} ctx 插件上下文。
 * @param {object} config 插件 Config。
 * @returns {object} 工具定义。
 */
export function createReviewTool(ctx, config) {
  return defineTool({
    name: 'ai_team_review',
    description:
      '把任务书送独立验收官（后端可配 codex / openai），产出三态裁决 pass / rework / escalate。' +
      '裁决只认结构化 JSON 字段；解析不出来、自相矛盾、证据缺失都会被降级（绝不默认 pass）。' +
      "⚠️ 只读**不是沙箱隔离**：返回值里 enforcement='snapshot_only' 就是在说这件事 —— " +
      '验收官的 prompt / 原文 / 裁决三份证据全落盘，只读靠事后可核（codex 沙箱档位由 Config.codexSandbox 定，默认 workspace-write）。' +
      'dryRun=true 只组装 prompt 落盘、不调用后端，返回值固定是 escalate（不是裁决）。',
    parameters: {
      brief: { type: 'string', required: true, description: '任务书内容，或以 .md 结尾时按路径读取' },
      round: { type: 'string', description: '轮次，默认 "1"' },
      backend: { type: 'string', description: 'codex / openai，默认取插件配置', enum: ['codex', 'openai'] },
      model: { type: 'string', description: '覆盖默认模型' },
      dryRun: { type: 'boolean', description: '只组装 prompt，不真调后端' },
    },
    output: jsonOutput({
      type: 'object',
      additionalProperties: false,
      properties: {
        verdict: { type: 'string', required: true, enum: ['pass', 'rework', 'escalate'] },
        confidence: { type: 'number', required: true },
        blocking: { type: 'array', required: true, items: { type: 'string' } },
        findings: {
          type: 'array',
          required: true,
          items: {
            type: 'object',
            additionalProperties: false,
            properties: {
              id: { type: 'string', required: true },
              claim: { type: 'string', required: true },
              evidence: { type: 'string', required: true },
              type: { type: 'string', required: true, enum: ['E0', 'E1', 'E2', 'E3'] },
            },
          },
        },
        outDir: { type: 'string', required: true },
        raw: { type: 'string', required: true },
        // 诚实标注：只读不是硬隔离，是「证据落盘 + 事后可核」。
        // 用 const 钉死，防止将来有人把某个后端说成隔离了。
        enforcement: { type: 'string', required: true, const: 'snapshot_only' },
      },
    }),
    timeoutMs: config.timeoutMs,
    async execute(args, exec) {
      const cwd = await cwdOf(ctx, exec);
      const round = String(args.round ?? '1').trim() || '1';
      if (!ROUND_PATTERN.test(round)) {
        throw new Error(`round 必须是正整数（最多 9 位），实际是 ${JSON.stringify(args.round)}`);
      }

      const backendName = String(args.backend ?? config.backend ?? 'codex')
        .trim()
        .toLowerCase();
      const adapter = selectBackend(backendName); // 名字不认识就在这里失败，不等到调用
      const model = typeof args.model === 'string' ? args.model.trim() : String(config.model ?? '').trim();

      const brief = resolveBriefInput(args.brief, cwd);
      const prompt = buildReviewPrompt({ briefText: brief.text, round, backend: backendName, cwd });

      const outDir = ensureDir(
        path.join(workspaceDir(cwd, config.outDirName), 'reviews', `${makeRunId('review')}-r${round}`),
      );
      writeFileSync(path.join(outDir, 'PROMPT.md'), prompt, 'utf8');
      if (brief.path) writeFileSync(path.join(outDir, 'BRIEF.path.txt'), `${brief.path}\n`, 'utf8');

      // ── dry-run：不调后端，也**不伪造**裁决 ────────────────────────────────
      if (args.dryRun === true) {
        const skipped = assertVerdict({
          verdict: 'escalate',
          confidence: 0,
          blocking: [
            'dry-run：没有调用后端，本结果不是裁决。prompt 已写入 outDir/PROMPT.md，' +
              '确认无误后用 dryRun=false 重跑。',
          ],
          findings: [],
        });
        writeFileSync(
          path.join(outDir, 'VERDICT.json'),
          `${JSON.stringify(
            {
              ...skipped,
              enforcement: ENFORCEMENT,
              round,
              backend: backendName,
              model,
              dryRun: true,
              at: new Date().toISOString(),
            },
            null,
            2,
          )}\n`,
          'utf8',
        );
        return { ...skipped, outDir, raw: '', enforcement: ENFORCEMENT };
      }

      // ── 真调后端 ───────────────────────────────────────────────────────────
      const result = await adapter.invoke({
        brief: prompt,
        model,
        timeoutMs: config.timeoutMs,
        signal: exec.signal,
        readOnly: true,
        cwd,
        ...backendOptions(config, backendName),
      });

      const raw = typeof result?.text === 'string' ? result.text : '';
      writeFileSync(path.join(outDir, 'RAW.md'), `${raw}\n`, 'utf8');
      writeFileSync(
        path.join(outDir, 'META.json'),
        `${JSON.stringify(
          {
            round,
            backend: backendName,
            model,
            cwd,
            enforcement: ENFORCEMENT,
            intent: '验收官被要求只读；这里如实记录「只读不是隔离」，落盘的三份证据才是可核的东西',
            at: new Date().toISOString(),
            usage: result?.usage ?? null,
            meta: result?.meta ?? null,
            rawChars: raw.length,
          },
          null,
          2,
        )}\n`,
        'utf8',
      );

      const verdict = assertVerdict(normalizeVerdict(parseVerdict(raw)));
      writeFileSync(
        path.join(outDir, 'VERDICT.json'),
        `${JSON.stringify(
          {
            verdict: verdict.verdict,
            confidence: verdict.confidence,
            blocking: verdict.blocking,
            findings: verdict.findings,
            enforcement: ENFORCEMENT,
            // notes 只落盘，不进返回值：它记录降级原因，供事后复核
            notes: verdict.notes ?? [],
            round,
            backend: backendName,
            model,
            at: new Date().toISOString(),
            rawChars: raw.length,
            rawIsEmpty: raw.trim() === '',
            noStructuredVerdict: (verdict.notes ?? []).includes('no-structured-verdict'),
            hint: raw.trim() === '' ? NO_VERDICT_BLOCKING : undefined,
          },
          null,
          2,
        )}\n`,
        'utf8',
      );

      return {
        verdict: verdict.verdict,
        confidence: verdict.confidence,
        blocking: verdict.blocking,
        findings: verdict.findings,
        outDir,
        raw: clip(raw, MAX_RAW_CHARS),
        enforcement: ENFORCEMENT,
      };
    },
  });
}
