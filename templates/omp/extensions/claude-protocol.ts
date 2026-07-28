// SPDX-License-Identifier: MIT
// claude-protocol OMP extension - mirrors the v3.x Claude-Code enforcement
// hooks in TypeScript using the OMP Extension API.
//
// Verified against can1357/oh-my-pi 14b5da7 (v16.5.1, 2026-07-14). API surface:
// packages/coding-agent/src/extensibility/extensions/types.ts
//   - pi.on("before_agent_start")    -> { systemPrompt: string[] }
//   - pi.on("tool_call", ...)         -> { block: true, reason: string }
//   - pi.on("tool_result", ...)      -> { content: [...], isError: true }
//   - pi.on("session.compacting", ...)-> { context: string[] }
// pi events are loaded via the extension runner in src/extensibility/extensions/loader.ts
// and can be registered with .omp/extensions/<name>.ts (TS) or .omp/hooks/{pre,post}/*.ts.

import { execFileSync } from "node:child_process";
import path from "node:path";

const RUNTIME = path.join(process.cwd(), ".omp", "shared", "runtime-policy.js");

let runtime = null;

async function loadRuntime() {
  if (runtime) return runtime;
  const url = await import("node:url").then((m) => m.pathToFileURL(RUNTIME).href);
  runtime = (await import(url)).default;
  return runtime;
}

function runCommand(command, args, cwd) {
  try {
    return execFileSync(command, args, {
      cwd,
      encoding: "utf8",
      timeout: 10_000,
      stdio: ["ignore", "pipe", "pipe"],
      shell: process.platform === "win32",
    }).trim();
  } catch {
    return "";
  }
}

function block(reason) {
  return { block: true, reason: `claude-protocol: ${reason}` };
}

export default async function claudeProtocol(pi) {
  const rt = await loadRuntime();

  pi.setLabel("Claude Protocol");

  pi.on("before_agent_start", async (_event, ctx) => {
    const prime = runCommand("bd", ["prime"], ctx.cwd);
    if (!prime) return;
    return { systemPrompt: [ctx.getSystemPrompt(), prime] };
  });

  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "edit" || event.toolName === "write") {
      const input = event.input ?? {};
      const filePath = String(input.path ?? input.filePath ?? "");
      const reason = rt.validateEdit({ cwd: ctx.cwd, filePath });
      if (reason) return block(reason);
    }
    if (event.toolName === "bash") {
      const command = String((event.input ?? {}).command ?? "");
      const reason = rt.validateBash(command);
      if (reason) return block(reason);
    }
  });

  pi.on("tool_result", async (event) => {
    if (event.toolName !== "task") return;
    const text = event.content
      .filter((part) => part && part.type === "text")
      .map((part) => part.text)
      .join("\n");
    const reason = rt.validateCompletion(text);
    if (!reason) return;
    return {
      content: [
        ...event.content,
        { type: "text", text: `claude-protocol validation: ${reason}` },
      ],
      isError: true,
    };
  });

  pi.on("session.compacting", async () => ({
    context: [
      "Preserve active bead IDs, bd worktree/branch state, pending checks, and PR/merge state. Run bd prime after compaction for the authoritative workflow context.",
    ],
  }));
}
