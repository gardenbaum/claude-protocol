// SPDX-License-Identifier: MIT
// Generated from templates/opencode/plugins/claude-protocol.ts by scripts/build-adapter.js
// (hand-rolled because we deliberately keep the build dependency-free).
// Edit the .ts source and re-run `node scripts/build-adapter.js` to regenerate.

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
  } catch { return ""; }
}

function fail(message) { throw new Error(`claude-protocol: ${message}`); }

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
      // Second-line check; primary enforcement is in tool.execute.before.
      // If the command field is missing from metadata, let the request
      // through — tool.execute.before will still see the bash call.
      if (input.permission !== "bash") return;
      const command = String((input.metadata && input.metadata.command) ?? "");
      if (command && rt.validateBash(command)) output.status = "deny";
    },
    "experimental.session.compacting": async (_input, output) => {
      output.context.push(
        "Before compaction, preserve active bead IDs, bd worktree/branch state, pending checks, and PR/merge state. Run bd prime after compaction for the authoritative workflow context.",
      );
    },
  };
};
