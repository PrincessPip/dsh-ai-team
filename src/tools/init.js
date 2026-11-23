/**
 * `ai_team_init` —— 把团队模板铺进当前工作区（SPEC §3.1）。
 *
 * 只做搬运，不生成内容：模板由包内 `templates/` 提供，缺模板就**报错**（不悄悄铺一半）。
 *
 * 铺法（SPEC §3.1 逐条）：
 *   templates/AGENTS.snippet.md → <target>/AI-TEAM.md
 *   templates/REVIEW-BRIEF.md   → <target>/REVIEW-BRIEF.md
 *   templates/INC-TEMPLATE.md   → <target>/INC-TEMPLATE.md
 *   templates/roles/*           → <target>/.ai-team/roles/*
 *
 * @module
 */
import { copyFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { jsonOutput } from '../lib/output.js';
import { cwdOf, ensureDir, resolveInCwd, templatesDirOf, workspaceDir } from '../lib/paths.js';

/** 顶层模板：源文件 → 目标相对路径。 */
const FIXED_TEMPLATES = Object.freeze([
  { source: 'AGENTS.snippet.md', target: 'AI-TEAM.md' },
  { source: 'REVIEW-BRIEF.md', target: 'REVIEW-BRIEF.md' },
  { source: 'INC-TEMPLATE.md', target: 'INC-TEMPLATE.md' },
]);

/**
 * @param {object} ctx 插件上下文。
 * @param {object} config 插件 Config。
 * @returns {object} `defineTool` 认的工具定义。
 */
export function createInitTool(ctx, config) {
  return defineTool({
    name: 'ai_team_init',
    description:
      '把 ai-team 团队模板铺进工作区：AI-TEAM.md（工作纪律摘要）、REVIEW-BRIEF.md（送审任务书模板）、' +
      'INC-TEMPLATE.md（事故单模板）、.ai-team/roles/{worker,reviewer,fire-chief}.md（角色卡）。' +
      '已存在的文件默认跳过（force=true 才覆盖）。',
    parameters: {
      target: { type: 'string', description: '目标目录，默认当前工作目录' },
      force: { type: 'boolean', description: '已存在是否覆盖，默认 false' },
    },
    output: jsonOutput({
      type: 'object',
      additionalProperties: false,
      properties: {
        created: { type: 'array', required: true, items: { type: 'string' } },
        skipped: { type: 'array', required: true, items: { type: 'string' } },
        root: { type: 'string', required: true },
      },
    }),
    timeoutMs: 60000,
    async execute(args, exec) {
      const cwd = await cwdOf(ctx, exec);
      const root = args.target ? resolveInCwd(args.target, cwd) : cwd;
      const force = args.force === true;

      const templatesDir = templatesDirOf();
      if (!existsSync(templatesDir) || !statSync(templatesDir).isDirectory()) {
        throw new Error(`包内模板目录不存在：${templatesDir}（安装不完整？整个模板集都不该缺失）`);
      }

      /** @type {{ source: string, target: string }[]} */
      const plan = [];
      const absent = [];
      for (const item of FIXED_TEMPLATES) {
        const source = path.join(templatesDir, item.source);
        if (!existsSync(source)) absent.push(item.source);
        else plan.push({ source, target: path.join(root, item.target) });
      }

      const rolesDir = path.join(templatesDir, 'roles');
      if (!existsSync(rolesDir) || !statSync(rolesDir).isDirectory()) {
        absent.push('roles/');
      } else {
        for (const entry of readdirSync(rolesDir)) {
          const source = path.join(rolesDir, entry);
          if (!statSync(source).isFile()) continue;
          plan.push({ source, target: path.join(workspaceDir(root, config.outDirName), 'roles', entry) });
        }
      }

      if (absent.length > 0) {
        // 铺一半比不铺更糟：用户会以为模板齐了
        throw new Error(`包内模板缺失（${templatesDir}）：${absent.join('、')}`);
      }

      const created = [];
      const skipped = [];
      for (const item of plan) {
        if (!force && existsSync(item.target)) {
          skipped.push(item.target);
          continue;
        }
        ensureDir(path.dirname(item.target));
        copyFileSync(item.source, item.target);
        created.push(item.target);
      }

      return { created, skipped, root };
    },
  });
}
