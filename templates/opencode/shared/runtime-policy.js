// SPDX-License-Identifier: MIT
// Runtime policy shared by the OpenCode and OMP adapters.
//
// Keep this dependency-free so both harnesses can load the generated
// adapters without installing a second package graph.

import { execFileSync } from "node:child_process";
import path from "node:path";

const DESCRIPTION_FLAGS = new Set(["-d", "--description", "--body-file", "--stdin"]);

function tokenize(command) {
  const tokens = [];
  let current = "";
  let quote = "";
  let escaped = false;

  for (const char of String(command ?? "")) {
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }
    if (char === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (char === quote) quote = "";
      else current += char;
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) tokens.push(current);
      current = "";
      continue;
    }
    current += char;
  }
  if (current) tokens.push(current);
  return tokens;
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

function currentBranch(cwd) {
  return runCommand("git", ["branch", "--show-current"], cwd);
}

function isAllowedOrchestrationPath(filePath) {
  const normalized = String(filePath ?? "").replaceAll("\\", "/");
  const base = path.posix.basename(normalized);
  if (["AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "git-issues.md"].includes(base)) return true;
  if (normalized.includes("/.worktrees/") || normalized.includes("/.claude/plans/")) return true;
  return normalized.includes("/.claude/") && normalized.includes("/memory/");
}

function validateEdit({ cwd, filePath }) {
  if (isAllowedOrchestrationPath(filePath)) return null;
  if (String(cwd ?? "").replaceAll("\\", "/").includes("/.worktrees/")) return null;
  const branch = currentBranch(cwd);
  if (branch === "main" || branch === "master") {
    return `Cannot edit files on ${branch}. Create a feature branch or a bd worktree first.`;
  }
  return null;
}

function isGitNoVerify(tokens) {
  if (tokens[0] !== "git") return false;
  return tokens.includes("--no-verify") || (tokens[1] === "commit" && tokens.includes("-n"));
}

function isRawGitWorktreeAdd(tokens) {
  return tokens[0] === "git" && tokens[1] === "worktree" && tokens[2] === "add";
}

function hasDescription(tokens) {
  return tokens.some(
    (token) => DESCRIPTION_FLAGS.has(token)
      || token.startsWith("--description=")
      || token.startsWith("--body-file="),
  );
}

function validateBash(command) {
  const tokens = tokenize(command);
  if (isGitNoVerify(tokens)) {
    return "git commit --no-verify is blocked. Run the checks and fix their failures.";
  }
  if (isRawGitWorktreeAdd(tokens)) {
    return "git worktree add is blocked. Use bd worktree create so Beads state stays consistent.";
  }
  if (tokens[0] === "bd" && ["create", "new"].includes(tokens[1]) && !hasDescription(tokens.slice(2))) {
    return "bd create requires -d/--description, --body-file, or --stdin for implementation context.";
  }
  return null;
}

function primeContext(cwd) {
  return runCommand("bd", ["prime"], cwd);
}

function validateCompletion(output) {
  const text = String(output ?? "");
  if (!/BEAD\s+[A-Za-z0-9._-]+\s+COMPLETE/.test(text)) {
    return "Subagent completion must include: BEAD {ID} COMPLETE.";
  }
  if (!/(Worktree:|Branch:).*bd-/i.test(text)) {
    return "Subagent completion must identify its bd worktree or branch.";
  }
  if (!text.includes("Checklist:")) {
    return "Subagent completion must include a Checklist section.";
  }
  if (/- \[ \]/.test(text)) {
    return "Subagent completion has unchecked checklist items.";
  }
  return null;
}

export default {
  validateEdit,
  validateBash,
  validateCompletion,
  primeContext,
  currentBranch,
};
