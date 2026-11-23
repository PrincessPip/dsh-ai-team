/**
 * `codex` 后端：把验收官跑在 `codex exec` 上（SPEC §5）。
 *
 * **沙箱档位可配、默认 `workspace-write`**（Config.codexSandbox）。为什么默认不是 read-only：
 * 工程自己实测过 —— `engine/runner.py:1436-1440` 原话是「`-s read-only` 在 Windows 上**禁一切命令**」，
 * 给了它验收官一个字节都读不到。所以本插件的「只读」**不是硬隔离**，而是「事后可核」：
 * 证据包（prompt / 原文 / 裁决）落盘 + 完整性与 writeScopes 事后核 → `ai_team_review` 返回
 * `enforcement: 'snapshot_only'` 就是在说这件事，**别把它读成隔离**。
 *
 * 调用形态照抄同仓库 `engine/review-codex.py:172-181`（本机实测跑通过的 argv）：
 *   node <codex.js> exec --json --skip-git-repo-check --sandbox <档位> -m <model> -C <cwd> -
 * prompt 走 **stdin**（`-`）—— 含空格/中文的 prompt 用 argv 传会被拆成多个参数。
 *
 * @module
 */
import { runProcess } from '../lib/proc.js';

/** 输出尾部截断长度（错误信息里只贴尾巴，别把整段 NDJSON 甩给模型）。 */
const TAIL = 1500;

/** codex CLI 认的三个沙箱档位（与 `codex exec --help` 的 possible values 一致）。 */
export const SANDBOX_MODES = Object.freeze(['read-only', 'workspace-write', 'danger-full-access']);

/** 默认档位：workspace-write（理由见文件头 —— read-only 在 Windows 上等于什么都干不了）。 */
export const DEFAULT_SANDBOX = 'workspace-write';

/** 从 `codex exec --json` 的 NDJSON 里取验收官正文：只认 completed 的 agent_message，取最后一条。 */
export function extractAgentText(stdout) {
  const texts = [];
  for (const line of String(stdout ?? '').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed.startsWith('{')) continue;
    let event;
    try {
      event = JSON.parse(trimmed);
    } catch {
      continue; // 非 JSON 行（进度条之类）不算错
    }
    const item = event?.item ?? null;
    // 主形状（实测）：{"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    if (event?.type === 'item.completed' && item?.type === 'agent_message' && typeof item.text === 'string') {
      texts.push(item.text);
      continue;
    }
    // 兜底形状：老版本/别的封装直接给 agent_message
    if (event?.type === 'agent_message' && typeof event.message === 'string') texts.push(event.message);
  }
  return texts.length > 0 ? texts[texts.length - 1] : '';
}

/** 尾巴：错误信息里带上它，否则「codex 失败」这句话等于没说。 */
function tail(text) {
  const s = String(text ?? '').trim();
  if (!s) return '（无输出）';
  return s.length <= TAIL ? s : `…${s.slice(-TAIL)}`;
}

/**
 * 组装 `codex exec` 的 argv（纯函数，便于机器核 —— 沙箱档位这种事不能只靠读代码）。
 *
 * 形状照抄 `engine/review-codex.py:172-181`（本机实测跑通过的 argv），只把
 * `--approve-for-me` 换成显式的 `--sandbox <档位>`：档位由 Config.codexSandbox 决定。
 *
 * @param {{ model?: string, sandbox?: string, cwd?: string }} [options]
 * @returns {string[]} argv（含结尾的 `-` = prompt 走 stdin）。
 */
export function buildCodexArgs(options = {}) {
  const sandboxMode = String(options.sandbox ?? DEFAULT_SANDBOX).trim().toLowerCase();
  if (!SANDBOX_MODES.includes(sandboxMode)) {
    throw new Error(`未知 codex 沙箱档位 "${String(options.sandbox)}"；可选：${SANDBOX_MODES.join(' / ')}`);
  }
  const args = ['exec', '--json', '--skip-git-repo-check', '--sandbox', sandboxMode];
  const modelName = typeof options.model === 'string' ? options.model.trim() : '';
  if (modelName) args.push('-m', modelName);
  if (options.cwd) args.push('-C', options.cwd);
  args.push('-'); // prompt 由 stdin 给
  return args;
}

/**
 * 调 codex 验收官。
 *
 * @param {object} options
 * @param {string} options.brief 组装好的验收 prompt（走 stdin）。
 * @param {string} [options.model] 模型名；留空用 codex 自己的默认。
 * @param {number} [options.timeoutMs] 超时（毫秒）。
 * @param {AbortSignal} [options.signal] 调用方取消信号（**必须传透**）。
 * @param {boolean} [options.readOnly] 契约占位；真正的权限由 `sandbox` 决定，这里只做记录。
 * @param {string} [options.sandbox='workspace-write'] 沙箱档位（read-only / workspace-write / danger-full-access）。
 * @param {string} [options.cwd] 验收官的工作目录（`-C`），默认进程 cwd。
 * @param {string} [options.codexPath='codex'] codex 可执行文件。
 * @returns {Promise<{ text: string, usage: null, meta: object }>}
 */
export async function invoke(options = {}) {
  const {
    brief,
    model,
    timeoutMs,
    signal,
    readOnly: _readOnly,
    sandbox = DEFAULT_SANDBOX,
    cwd,
    codexPath = 'codex',
  } = options;

  if (typeof brief !== 'string' || brief.trim() === '') {
    throw new Error('codex 后端需要非空的 brief（验收 prompt）');
  }

  const args = buildCodexArgs({ model, sandbox, cwd });
  const sandboxMode = args[args.indexOf('--sandbox') + 1];
  const modelName = typeof model === 'string' ? model.trim() : '';

  const result = await runProcess(codexPath, args, {
    cwd,
    timeoutMs,
    signal,
    input: brief,
  });

  const text = extractAgentText(result.stdout);

  if (result.timedOut) {
    throw new Error(
      `codex 验收官超时（${timeoutMs} ms 被强制结束，已 kill 进程树）。stderr 尾部：${tail(result.stderr)}`,
    );
  }
  if (!text) {
    throw new Error(
      `codex 验收官没有返回任何文本（exit=${result.code}）。` +
        `stderr 尾部：${tail(result.stderr)} ／ stdout 尾部：${tail(result.stdout)}`,
    );
  }
  if (result.code !== 0) {
    // 有正文但退出码非 0：可能是会话末了的非致命错误 —— 不吞，但也不把正文丢掉
    return {
      text,
      usage: null,
      meta: {
        backend: 'codex',
        model: modelName || '(codex 默认)',
        argv: args.slice(0, -1).join(' '),
        exitCode: result.code,
        durationMs: result.durationMs,
        sandbox: sandboxMode,
        caveat: 'sandbox 是请求档位，不是「验收官没写文件」的证明 —— 只读执法靠证据包 + 事后快照核',
        note: `codex 退出码 ${result.code}，但已拿到正文；stderr 尾部：${tail(result.stderr)}`,
      },
    };
  }

  return {
    text,
    usage: null,
    meta: {
      backend: 'codex',
      model: modelName || '(codex 默认)',
      argv: args.slice(0, -1).join(' '),
      exitCode: result.code,
      durationMs: result.durationMs,
      sandbox: sandboxMode,
      notion: 'sandbox 是请求档位，不是「验收官没写文件」的证明 —— 只读执法靠证据包 + 事后快照核',
      truncated: result.truncated,
    },
  };
}
