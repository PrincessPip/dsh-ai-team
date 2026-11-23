/**
 * 子进程封装：超时 / 取消 / 杀进程树 / UTF-8 解码 / 输出上限。
 *
 * 为什么必须包一层（不是「顺手加个 helper」）：
 * 1. **Windows 上超时杀不干净**。`child.kill()` 只杀直接子进程，Python/Node 引擎起的孙进程
 *    （`dsh` launcher、`codex`、假员工）会继续跑、继续写 `runs/`，把下一次调用判成「目录已存在」。
 *    所以超时/取消一律走 `taskkill /T /F /PID`（整棵树），POSIX 侧用 `detached` + 杀进程组。
 *    ⚠️ **`engine/` 里没有现成实现可抄**（实测全仓 0 个 `taskkill`）：Python 侧靠 Windows Job Object
 *    （`engine/review-snapshot.py:136/140/147-151`，非 Windows 直接 raise）+ 两处光秃秃的
 *    `p.kill()`（`engine/fire-chief.py:635-646`，**不杀孙进程**）。所以这一层必须 Node 侧自己实现。
 * 2. **编码不能靠猜**。引擎输出含中文，Windows 默认代码页会把 UTF-8 读成 GBK。
 *    这里按 **Buffer 收集、最后一次性按 utf8 解码**，不交给流上的编码假设。
 * 3. **输出可能很大**（`runner.py` 会打几百行），必须有上限，否则一次真跑能吃光内存。
 *
 * 契约：
 *   `runProcess(command, args, options)` → `Promise<Result>`
 *   Result = { code, signal, stdout, stderr, timedOut, aborted, durationMs, truncated }
 *   - `code` 为 `number | null`（被信号杀掉时是 `null`）；要判成功请用 `code === 0`。
 *   - 子进程**起不来**（ENOENT 等）与 **调用方取消**（AbortSignal）走 reject；
 *     超时不 reject —— 由调用方决定「超时」在它那条链上算什么（Gate 读不到图 ≠ 工具崩溃）。
 *
 * @module
 */
import { spawn } from 'node:child_process';

/** 单次调用默认最多保留的输出字节数（stdout / stderr 各自计）。 */
export const DEFAULT_MAX_CAPTURE = 2 * 1024 * 1024;

/** 杀进程树时等 `taskkill` 落地的上限：它自己也会卡（比如进程处于不可中断状态）。 */
const KILL_TIMEOUT_MS = 10000;

/** 收字节 + 截断。`truncated` 只回答「有没有丢过东西」，丢了多少记在返回体里。 */
function appendCapture(state, chunk) {
  state.total += chunk.length;
  const remain = state.max - state.kept;
  if (remain <= 0) return;
  if (chunk.length <= remain) {
    state.chunks.push(chunk);
    state.kept += chunk.length;
  } else {
    state.chunks.push(chunk.subarray(0, remain));
    state.kept += remain;
  }
}

/** 拼成字符串；被截断时补一句「后面还有多少字节没要」，不假装输出只有这么长。 */
function decodeCapture(state) {
  const text = Buffer.concat(state.chunks).toString('utf8');
  const dropped = state.total - state.kept;
  if (dropped <= 0) return { text, truncated: false };
  return { text: `${text}\n…[输出被截断：还有 ${dropped} 字节未收集]`, truncated: true };
}

/** 起不来（ENOENT / EACCES / EINVAL）时给一句能照着改的话，而不是裸的 ENOENT。 */
function spawnError(command, error) {
  const code = error && error.code ? error.code : 'unknown';
  const hint =
    code === 'ENOENT'
      ? '（找不到这个可执行文件：检查插件 Config 里的路径，或把它放进 PATH）'
      : code === 'EACCES'
        ? '（没有执行权限）'
        : '';
  const wrapped = new Error(`无法启动子进程：${command}（${code}）${hint}`);
  wrapped.code = code;
  wrapped.cause = error;
  return wrapped;
}

/**
 * 杀掉整棵进程树。
 * Windows：`taskkill /T /F` —— `/T` 是「连孙进程一起」，少了它等于没杀。
 * POSIX：`detached: true` 让子进程自成进程组，`kill(-pid)` 杀整组。
 */
async function killTree(child, windows) {
  const pid = child.pid;
  if (!pid) return;
  if (windows) {
    await new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      let killer;
      try {
        killer = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], {
          windowsHide: true,
          stdio: 'ignore',
        });
      } catch {
        finish();
        return;
      }
      killer.once('error', finish);
      killer.once('close', finish);
      const timer = setTimeout(finish, KILL_TIMEOUT_MS);
      if (typeof timer.unref === 'function') timer.unref();
    });
    return;
  }
  try {
    process.kill(-pid, 'SIGKILL');
  } catch {
    try {
      child.kill('SIGKILL');
    } catch {
      /* 已经死了，或者根本没起来 —— 都不需要再报 */
    }
  }
}

/**
 * 跑一个子进程并等它结束。
 *
 * @param {string} command 可执行文件（走 PATH 解析；Windows 上请给 `.exe`/`.cmd` 或完整路径）。
 * @param {string[]} [args] 参数数组，不经 shell —— 含空格/中文的参数不会被拆。
 * @param {object} [options]
 * @param {string} [options.cwd] 工作目录。
 * @param {Record<string, string>} [options.env] 追加/覆盖的环境变量（其余继承 `process.env`）。
 * @param {number} [options.timeoutMs] 超时毫秒；`0`/省略 = 不限时。
 * @param {AbortSignal} [options.signal] 调用方取消信号（**必须传透**，别在这里断开）。
 * @param {string} [options.input] 写进 stdin 的内容（`codex exec -` 之类的入口靠它拿 prompt）。
 * @param {number} [options.maxCapture] stdout/stderr 各自最多保留多少字节。
 * @returns {Promise<{ code: number|null, signal: string|null, stdout: string, stderr: string,
 *   timedOut: boolean, aborted: boolean, durationMs: number, truncated: boolean }>}
 */
export function runProcess(command, args = [], options = {}) {
  const {
    cwd,
    env,
    timeoutMs = 0,
    signal,
    input,
    maxCapture = DEFAULT_MAX_CAPTURE,
  } = options;

  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    const windows = process.platform === 'win32';
    const out = { chunks: [], kept: 0, total: 0, max: maxCapture };
    const err = { chunks: [], kept: 0, total: 0, max: maxCapture };
    let timedOut = false;
    let aborted = false;
    let settled = false;

    let child;
    try {
      child = spawn(command, args, {
        cwd,
        env: { ...process.env, ...(env || {}) },
        windowsHide: true,
        // POSIX 侧要自成进程组才能整组杀；Windows 侧 detached 会另开控制台，不能设。
        detached: !windows,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      reject(spawnError(command, error));
      return;
    }

    let timer = null;
    const onAbort = () => {
      aborted = true;
      void killTree(child, windows);
    };

    const cleanup = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      if (signal) signal.removeEventListener('abort', onAbort);
    };

    child.stdout?.on('data', (chunk) => appendCapture(out, Buffer.from(chunk)));
    child.stderr?.on('data', (chunk) => appendCapture(err, Buffer.from(chunk)));
    // 子进程提前退出时写 stdin 会 EPIPE —— 这不是错误，交给 close 事件判结局。
    child.stdin?.on('error', () => {});

    child.once('error', (error) => {
      if (settled) return;
      settled = true;
      cleanup();
      void killTree(child, windows);
      reject(spawnError(command, error));
    });

    child.once('close', (code, sig) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (aborted) {
        const cancelled = new Error(`子进程已取消：${command}`);
        cancelled.name = 'AbortError';
        reject(cancelled);
        return;
      }
      const stdout = decodeCapture(out);
      const stderr = decodeCapture(err);
      resolve({
        code: typeof code === 'number' ? code : null,
        signal: sig ?? null,
        stdout: stdout.text,
        stderr: stderr.text,
        timedOut,
        aborted,
        durationMs: Date.now() - startedAt,
        truncated: stdout.truncated || stderr.truncated,
      });
    });

    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        timedOut = true;
        void killTree(child, windows);
      }, timeoutMs);
      if (typeof timer.unref === 'function') timer.unref();
    }

    if (signal) {
      if (signal.aborted) {
        aborted = true;
        void killTree(child, windows);
      } else {
        signal.addEventListener('abort', onAbort, { once: true });
      }
    }

    try {
      if (typeof input === 'string' && input.length > 0) child.stdin.end(input, 'utf8');
      else child.stdin.end();
    } catch {
      /* stdin 关掉就算了，结局由 close/error 决定 */
    }
  });
}
