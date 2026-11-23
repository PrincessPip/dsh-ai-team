#!/usr/bin/env node
// fake-codex/codex.js —— a stand-in for the real codex CLI, for the assertion bench only.
//
// Copied argv shape:  node <this> exec --json --skip-git-repo-check -C <ROOT> --approve-for-me -
// So it reads the prompt from stdin and writes codex-style JSONL to stdout.
//
// Why this file exists (2026-11-11, round-1 reviewer B4):
//   "Deleting the proxy injection line leaves the whole bench green." The bench could not see the
//   child process environment at all, because every codex test stopped at `worker_argv()` (a pure
//   function). This stand-in echoes the proxy it actually received back into its own tool_result,
//   so `proxy injection happened` becomes a fact the bench can assert.
//
// Two behaviours, driven by the prompt itself (no counter files, no clock):
//   * no `## 上一次尝试为什么失败` section  -> answer WITHOUT a <RESULT> block  (attempt fails)
//   * that section present                  -> answer WITH <RESULT>            (attempt succeeds)
// That deliberately reuses the retry-prompt mechanism, so the escalate path is exercised by the
// same code that a real retry uses.
//
// Keep this file pure ASCII (PowerShell 5.1 decodes non-ASCII scripts as 936; a stray Chinese
// comment once silently killed two assignments in this repo).

"use strict";

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => {
  input += d;
});
process.stdin.on("end", () => {
  const m = input.match(/^id:\s*(\S+)/m);
  const nid = m ? m[1] : "unknown";
  const hasPrev = input.indexOf("## 上一次尝试为什么失败") >= 0;
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy || "";

  emit({ type: "thread.started", thread_id: "fake-" + nid + "-" + String(process.pid) });
  emit({ type: "turn.started" });
  emit({
    type: "item.started",
    item: {
      id: "item_1",
      type: "command_execution",
      command: "powershell.exe -Command $env:HTTPS_PROXY",
      aggregated_output: "",
      exit_code: null,
      status: "in_progress",
    },
  });
  emit({
    type: "item.completed",
    item: {
      id: "item_1",
      type: "command_execution",
      command: "powershell.exe -Command $env:HTTPS_PROXY",
      // The single fact this stand-in exists for: what the CHILD process really saw.
      aggregated_output: "HTTPS_PROXY=" + proxy,
      exit_code: 0,
      status: "completed",
    },
  });
  const text = hasPrev
    ? "fake codex: retry seen\n<RESULT>{\"summary\":\"fake ok after retry\"}</RESULT>"
    : "fake codex: no RESULT on purpose";
  emit({ type: "item.completed", item: { id: "item_2", type: "agent_message", text: text } });
  emit({ type: "turn.completed", usage: { input_tokens: 1, output_tokens: 1 } });
});
