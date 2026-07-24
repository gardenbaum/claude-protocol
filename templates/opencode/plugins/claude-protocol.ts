// SPDX-License-Identifier: MIT
// claude-protocol OpenCode plugin - mirrors the v3.x Claude-Code enforcement hooks in JS.
//
// Verified against OpenCode v1.18.0 / @opencode-ai/plugin (anomalyco/opencode d6f1b08).
// Plugin contract ref: packages/plugin/src/index.ts: Hooks interface.
//
//   - "tool.execute.before"   mutates args; throwing blocks the tool
//   - "tool.execute.after"    mutates the result; non-throwing returns success
//   - "permission.ask"        returns { status: "deny" | "ask" | "allow" }
//   - "experimental.chat.system.transform"  appends to system[] before LLM call
//   - "experimental.session.compacting"     appends context[] before compaction prompt
//
// The plugin is loaded as a single .ts/.js file from .opencode/plugins/ per
// packages/web/src/content/docs/plugins.mdx; Bun handles the import at startup.

import { execFileSync } from "node:child_process";
import path from "node:path";

const PROJECT_DIR = process.env.CLAUDE_PROTOCOL_PROJECT_DIR ?? process.cwd();
const RUNTIME = path.join(PROJECT_DIR, ".opencode", "shared", "runtime-policy.js");

let runtime = null;

async function loadRuntime() {
  if (runtime) return runtime;
  const fileUrl = await import("node:url").then((m) => m.pathToFileURL(RUNTIME).href);
  runtime = (await import(fileUrl)).default;
  return runtime;
}

function runCommand(command, args) {
  try {
    return execFileSync(command, args, {
      cwd: PROJECT_DIR,
      encoding: "utf8",
      timeout: 10_000,
      stdio: ["ignore", "pipe", "pipe"],
      shell: process.platform === "win32",
    }).trim();
  } catch {
    return "";
  }
}

function fail(message) {
  throw new Error(`claude-protocol: ${message}`);
}

export const ClaudeProtocol = async ({ project, directory }) => {
  const cwd = directory ?? project?.worktree ?? PROJECT_DIR;
  const rt = await loadRuntime();

  return {
    "experimental.chat.system.transform": async (_input, output) => {
      const prime = runCommand("bd", ["prime"]);
      if (prime) output.system.push(prime);
    },

    "tool.execute.before": async (input, output) => {
      if (input.tool === "edit" || input.tool === "write") {
        const filePath = String(output.args?.filePath ?? "");
        const reason = rt.validateEdit({ cwd, filePath });
        if (reason) fail(reason);
      }
      if (input.tool === "bash") {
        const reason = rt.validateBash(String(output.args?.command ?? ""));
        if (reason) fail(reason);
      }
    },

    "tool.execute.after": async (input, output) => {
      if (input.tool !== "task") return;
      const text = String(output.output ?? "");
      const reason = rt.validateCompletion(text);
      if (!reason) return;
      output.output = `${text}\n\n<claude-protocol-validation-error>${reason}</claude-protocol-validation-error>`;
      output.metadata = { ...(output.metadata || {}), claudeProtocolValidation: "failed" };
    },

    "permission.ask": async (input, output) => {
      if (input.permission !== "bash") return;
      const command = String(input.metadata?.command ?? "");
      if (rt.validateBash(command)) output.status = "deny";
    },

    "experimental.session.compacting": async (_input, output) => {
      output.context.push(
        "Before compaction, preserve active bead IDs, bd worktree/branch state, pending checks, and PR/merge state. Run bd prime after compaction for the authoritative workflow context.",
      );
    },
  };
};
