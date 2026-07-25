#!/usr/bin/env node
'use strict';

// PreToolUse: Bash — Git safety, bd validation, epic close checks
// Consolidated from: validate-epic-close + block-orchestrator-tools (Bash logic)

const {
  readStdinJSON, getField, deny, isSubagent,
  execCommand, execCommandJSON, runHook,
} = require('./hook-utils.cjs');

// Shell-style tokenizer (subset of POSIX shell rules). Returns an array of
// { value, quoted } where `quoted=true` for tokens that originated inside
// '...' or "...". Used to distinguish a top-level flag like `--no-verify`
// from the same string appearing as a substring of a quoted commit message.
function _shellTokens(cmd) {
  const out = [];
  let i = 0, cur = '', wasQuoted = false, inSingle = false, inDouble = false;
  while (i < cmd.length) {
    const c = cmd[i];
    if (c === "'" && !inDouble) { inSingle = !inSingle; wasQuoted = true; i++; continue; }
    if (c === '"' && !inSingle) { inDouble = !inDouble; wasQuoted = true; i++; continue; }
    if (/\s/.test(c) && !inSingle && !inDouble) {
      if (cur) {
        out.push({ value: cur, quoted: wasQuoted });
        cur = '';
        wasQuoted = false;
      }
      i++;
      continue;
    }
    cur += c;
    i++;
  }
  if (cur) out.push({ value: cur, quoted: wasQuoted });
  return out;
}

runHook('bash-guard', () => {
  const input = readStdinJSON();

  // Subagents get full access
  if (isSubagent(input)) process.exit(0);

  // Get command — prefer env var (original behavior), fall back to stdin
  let toolInput;
  try {
    toolInput = process.env.CLAUDE_TOOL_INPUT
      ? JSON.parse(process.env.CLAUDE_TOOL_INPUT)
      : getField(input, 'tool_input') || {};
  } catch {
    toolInput = getField(input, 'tool_input') || {};
  }

  const command = toolInput.command || '';
  const firstWord = command.split(/\s+/)[0] || '';

  // === Git safety checks ===
  if (firstWord === 'git') {
    // Match `--no-verify` only as a top-level flag, not as a substring of a
    // commit message or branch name. We shell-tokenize (respecting single-
    // and double-quoted strings) and look for `--no-verify` only among
    // unquoted arguments. So `git commit -m "explain --no-verify"` is fine.
    const noVerifyAsFlag = _shellTokens(command).some(
      (tok) => tok.value === '--no-verify' && !tok.quoted
    );
    if (noVerifyAsFlag || /\bcommit\b.*\s-n\b/.test(command)) {
      deny(
        'git commit --no-verify is blocked.\n\n' +
        'Pre-commit hooks exist for a reason (type-check, lint, tests).\n' +
        'Run the commit without --no-verify and fix any issues.'
      );
    }

    // Block raw `git worktree add` — it creates a shadow .beads/ (process leak,
    // data loss). Match by argument structure (subcommand=worktree, action=add),
    // not a naive includes('add'), so branch/path names containing "add" and
    // `git worktree remove`/`prune`/`list` are unaffected.
    const gitSubArgs = command.split(/\s+/).filter(Boolean).slice(1);
    if (gitSubArgs[0] === 'worktree' && gitSubArgs[1] === 'add') {
      deny(
        'git worktree add is blocked — use `bd worktree create` instead.\n\n' +
        'Raw `git worktree add` creates a shadow .beads/ copy (process leak, data loss).\n' +
        'To remove a worktree, prefer `bd worktree remove` (it has safety checks); ' +
        'raw `git worktree remove` is a fallback.'
      );
    }

    process.exit(0);
  }

  // === bd validation ===
  if (firstWord === 'bd') {
    const parts = command.split(/\s+/);
    const subCmd = parts[1] || '';

    // bd create must have description. Description-equivalent flags
    // (Beads v1.0.5+): -d/--description, --body-file/--stdin, --design,
    // --design-file, --acceptance, --context, --notes, --append-notes.
    // Any one is enough — supervisors need *some* context for the bead.
    if (subCmd === 'create' || subCmd === 'new') {
      const hasDescription = parts.some(
        (token) => token === '-d'
        || token === '--description'
        || token === '--stdin'
        || token === '--body-file'
        || token === '--design'
        || token === '--design-file'
        || token === '--acceptance'
        || token === '--context'
        || token === '--notes'
        || token === '--append-notes'
        || token.startsWith('--description=')
        || token.startsWith('--body-file=')
        || token.startsWith('--design=')
        || token.startsWith('--design-file=')
        || token.startsWith('--acceptance=')
        || token.startsWith('--context=')
        || token.startsWith('--notes=')
        || token.startsWith('--append-notes=')
      );
      if (!hasDescription) {
        deny('bd create requires description (-d, --description, --body-file, --stdin, --design, --design-file, --acceptance, --context, --notes, or --append-notes) for supervisor context.');
      }
    }

    // bd q (Quick-Capture) auto-generates a placeholder description; do not
    // require one. Otherwise, when the user wants to capture a half-formed
    // thought fast, the hook would block them on a flag they don't know
    // about. The full `bd create` still requires a description.
    if (subCmd === 'q') {
      return;
    }

    // === Epic close validation ===
    if (subCmd === 'close') {
      // --force is only an opt-out if it appears as a standalone flag
      // anywhere in the argv. To avoid a malicious description
      // ("--force") bypassing the audit, we tokenize and accept any of:
      //   - a flag anywhere in the command: `bd close E --force`
      //   - but NOT inside a description value (`-d "..."` or
      //     `--description=...`) or bead id.
      // The simplest safe check: only the immediate bead id
      // (parts[2]) plus standalone flags before any -d/--description
      // qualify. The position-aware check below matches `bd close ID
      // [flags]` and rejects `--force` inside the description.
      const closeId = parts[2] || '';
      if (!closeId || !/^[A-Za-z0-9._-]+$/.test(closeId)) process.exit(0);

      // Find the index of the description flag (-d, --description,
      // --body-file, --stdin, --design, --design-file, --acceptance,
      // --context, --notes, --append-notes) — anything beyond that
      // index is "description content" and not a flag.
      let descStart = parts.length;
      for (let i = 2; i < parts.length; i++) {
        const t = parts[i];
        if (t === '-d' || t === '--description' || t === '--body-file'
            || t === '--stdin' || t === '--design' || t === '--design-file'
            || t === '--acceptance' || t === '--context' || t === '--notes'
            || t === '--append-notes') {
          descStart = i + 1;
          // If the flag is `-d X` (with separate arg), skip X too.
          if (t === '-d' || t === '--description' || t === '--body-file'
              || t === '--design' || t === '--design-file'
              || t === '--acceptance' || t === '--context' || t === '--notes'
              || t === '--append-notes') {
            // Some flags take a value (skip it). --stdin reads from
            // stdin and has no argv value.
            if (i + 1 < parts.length) descStart = i + 2;
          }
          break;
        }
        if (t.startsWith('--description=') || t.startsWith('--body-file=')
            || t.startsWith('--design=') || t.startsWith('--design-file=')
            || t.startsWith('--acceptance=') || t.startsWith('--context=')
            || t.startsWith('--notes=') || t.startsWith('--append-notes=')) {
          descStart = i + 1;
          break;
        }
      }
      // --force is only valid in the flag section, before description.
      const flagSection = parts.slice(2, descStart);
      const hasForceFlag = flagSection.some(
        (t) => t === '--force' || t.startsWith('--force=')
      );
      if (hasForceFlag) process.exit(0);

      const closeMatch = command.match(/bd\s+close\s+([A-Za-z0-9._-]+)/);
      if (!closeMatch) process.exit(0);
      // closeId already validated above

      // CHECK 1: PR merge validation
      const branch = `bd-${closeId}`;
      const hasRemote = execCommand('git', ['remote', 'get-url', 'origin']);

      if (hasRemote) {
        const remoteBranch = execCommand('git', ['ls-remote', '--heads', 'origin', branch]);
        if (remoteBranch) {
          const mergedPr = execCommand('gh', [
            'pr', 'list', '--head', branch, '--state', 'merged',
            '--json', 'number', '--jq', '.[0].number',
          ]);
          if (!mergedPr) {
            deny(
              `Cannot close bead '${closeId}' — branch '${branch}' has no merged PR. ` +
              `Create and merge a PR first, or use 'bd close ${closeId} --force' to override.`
            );
          }
        }
      }

      // CHECK 2: Epic children validation
      const beadData = execCommandJSON('bd', ['show', closeId, '--json']);
      const issueType = beadData && beadData[0] ? (beadData[0].issue_type || '') : '';

      if (issueType === 'epic') {
        // --all includes closed children (default is open-only); without it
        // we'd silently approve an epic close when all children are already
        // closed, because they'd be missing from the default scan.
        const allBeads = execCommandJSON('bd', ['list', '--json', '--all']);
        if (Array.isArray(allBeads)) {
          const prefix = closeId + '.';
          const incomplete = allBeads.filter(
            b => b.id && b.id.startsWith(prefix) && b.status !== 'closed'
          );
          if (incomplete.length > 0) {
            const list = incomplete.map(b => `${b.id} (${b.status})`).join(', ');
            deny(
              `Cannot close epic '${closeId}' - has ${incomplete.length} incomplete children: ${list}. ` +
              'Mark all children as done first.'
            );
          }
        }
      }
    }

    process.exit(0);
  }

  // Allow everything else
  process.exit(0);
});
