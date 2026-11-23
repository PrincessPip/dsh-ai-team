/**
 * 后端选择（SPEC §5）。验收官可插拔：`codex` / `openai`，都只要实现同一个 `invoke()`。
 *
 * 加一个后端 = 加一个模块 + 在 `ADAPTERS` 里挂一行；工具层不认识任何具体后端。
 *
 * @module
 */
import * as codex from './codex-cli.js';
import * as openai from './openai-compat.js';

/** 已注册的后端。键 = 模型在参数里写的名字。 */
export const ADAPTERS = Object.freeze({ codex, openai });

/** 可选后端名（给错误信息用）。 */
export const BACKEND_NAMES = Object.freeze(Object.keys(ADAPTERS));

/** 默认后端（与 Config.backend 默认值一致）。 */
export const DEFAULT_BACKEND = 'codex';

/**
 * 按名字取后端；不认识就抛错并列出可用项（不要静默回落到 codex —— 那会换掉执行的模型）。
 *
 * @param {string} [name] 后端名。
 * @returns {{ invoke: (options: object) => Promise<{ text: string, usage: unknown, meta: object }> }}
 */
export function selectBackend(name) {
  const key = String(name ?? DEFAULT_BACKEND)
    .trim()
    .toLowerCase();
  const adapter = ADAPTERS[key];
  if (!adapter) {
    throw new Error(`未知验收后端 "${String(name)}"；可选：${BACKEND_NAMES.join(' / ')}`);
  }
  return adapter;
}

/**
 * 统一入口：把 `backend` 从参数里取出来后转发给对应 adapter。
 *
 * @param {object} options
 * @param {string} options.backend 后端名（codex / openai）。
 * @returns {Promise<{ text: string, usage: unknown, meta: object }>}
 */
export async function invokeBackend(options = {}) {
  const { backend, ...rest } = options;
  return selectBackend(backend).invoke(rest);
}
