/**
 * `openai` 后端：任意 OpenAI 兼容的 `/chat/completions`（SPEC §5）。
 *
 * 两条硬约束：
 * 1. **凭据只从环境变量读**。`apiKeyEnv` 是变量**名**（例如 `OPENAI_API_KEY`），
 *    不是 key 本身；key 从不进返回值、不进错误信息、不进日志。
 * 2. **纯文本后端没有文件读取能力**。所以它拿到什么就判什么 —— prompt 里必须带工作区快照，
 *    而本 MVP 只带任务书正文（这一限制写在工具 description 与 `docs/PORTING.md` 里，不藏着）。
 *
 * 失败语义（与 SPEC §5 一致）：连不上 / 鉴权失败 / 空回复 → 抛 `Error`（工具失败，模型看得见）；
 * 后端能用但裁决不合规 → 由 `verdict.js` 降级，不在这里处理。
 *
 * @module
 */
import { assertEnvName, clip } from '../lib/paths.js';

/**
 * 调 OpenAI 兼容后端。
 *
 * @param {object} options
 * @param {string} options.brief 组装好的验收 prompt。
 * @param {string} [options.model] 模型名；留空则由服务端默认（多数服务端会 400，请在 Config 里配）。
 * @param {number} [options.timeoutMs] 超时（毫秒），默认 600000。
 * @param {AbortSignal} [options.signal] 调用方取消信号（**必须传透**）。
 * @param {boolean} [options.readOnly=true] 恒定只读（本后端没有写文件能力，参数只为对齐契约）。
 * @param {string} [options.baseUrl='https://api.openai.com/v1'] API 基址。
 * @param {string} [options.apiKeyEnv='OPENAI_API_KEY'] **存放 key 的环境变量名**。
 * @returns {Promise<{ text: string, usage: object|null, meta: object }>}
 */
export async function invoke(options = {}) {
  const {
    brief,
    model,
    timeoutMs = 600000,
    signal,
    readOnly: _readOnly = true,
    baseUrl = 'https://api.openai.com/v1',
    apiKeyEnv = 'OPENAI_API_KEY',
  } = options;

  if (typeof brief !== 'string' || brief.trim() === '') {
    throw new Error('openai 后端需要非空的 brief（验收 prompt）');
  }

  const envName = assertEnvName(apiKeyEnv);
  const apiKey = process.env[envName];
  if (!apiKey) {
    // 注意：这里只说变量名，不打印值，也不猜用户是不是把 key 写进了配置
    throw new Error(
      `openai 后端缺少凭据：环境变量 ${envName} 未设置。` +
        '本插件只从环境变量读 key（Config.openaiApiKeyEnv 是变量名，不是 key 本身）。',
    );
  }

  const url = `${String(baseUrl).replace(/\/+$/, '')}/chat/completions`;
  const controller = new AbortController();
  let timedOut = false;

  const timer =
    timeoutMs > 0
      ? setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, timeoutMs)
      : null;
  if (timer && typeof timer.unref === 'function') timer.unref();

  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', onAbort, { once: true });
  }

  const modelName = typeof model === 'string' ? model.trim() : '';
  const startedAt = Date.now();

  try {
    let response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          ...(modelName ? { model: modelName } : {}),
          messages: [{ role: 'user', content: brief }],
          stream: false,
        }),
        signal: controller.signal,
      });
    } catch (error) {
      if (timedOut) throw new Error(`openai 后端超时（${timeoutMs} ms）：${url}`);
      if (signal?.aborted) {
        const cancelled = new Error('openai 后端调用已取消');
        cancelled.name = 'AbortError';
        throw cancelled;
      }
      throw new Error(`openai 后端连不上（${url}）：${error?.message ?? error}`);
    }

    if (!response.ok) {
      // 响应体可能含服务端的错误码，但它**不会**含我们的 key —— 只贴前 800 字符
      let body = '';
      try {
        body = await response.text();
      } catch {
        body = '（响应体读取失败）';
      }
      throw new Error(
        `openai 后端返回 HTTP ${response.status} ${response.statusText || ''}`.trim() + `：${clip(body, 800)}`,
      );
    }

    let data;
    try {
      data = await response.json();
    } catch (error) {
      throw new Error(`openai 后端返回的不是 JSON：${error?.message ?? error}`);
    }

    const content = data?.choices?.[0]?.message?.content;
    const text = typeof content === 'string' ? content : '';
    if (text.trim() === '') {
      throw new Error('openai 后端返回了空内容（choices[0].message.content 为空）');
    }

    return {
      text,
      usage: data?.usage ?? null,
      meta: {
        backend: 'openai',
        model: modelName || '(服务端默认)',
        endpoint: url,
        durationMs: Date.now() - startedAt,
        readonlyEnforcement: 'none — 纯文本后端没有文件/命令能力',
        note: '未提供工作区快照：本后端看不到文件，只能基于 prompt 里的任务书判断',
      },
    };
  } finally {
    if (timer) clearTimeout(timer);
    if (signal) signal.removeEventListener('abort', onAbort);
  }
}
