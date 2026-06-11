#!/usr/bin/env node
'use strict';

// SessionStart: orchestration status only (dirty-main / merged-worktree / open PRs).
// bd's own `bd prime --hook-json` SessionStart hook owns workflow context + beads.

const fs = require('fs');
const path = require('path');
const { injectText, execCommand, getProjectDir, getRepoRoot, runHook } = require('./hook-utils.cjs');

runHook('session-start', () => {
  const projectDir = getProjectDir();
  const beadsDir = path.join(projectDir, '.beads');

  if (!fs.existsSync(beadsDir)) {
    injectText("No .beads directory found. Run 'bd init' to initialize.\n");
    process.exit(0);
  }

  // Check if bd is available
  if (!execCommand('bd', ['--version'])) {
    injectText('beads CLI (bd) not found. Install from: https://github.com/gastownhall/beads\n');
    process.exit(0);
  }

  const output = [];

  // ============================================================
  // Dirty Parent Check
  // ============================================================
  // getRepoRoot resolves to the MAIN repo root even when projectDir is a
  // linked worktree (CLAUDE_PROJECT_DIR may point at .worktrees/bd-X).
  const repoRoot = getRepoRoot(projectDir);
  if (repoRoot) {
    const dirty = execCommand('git', ['-C', repoRoot, 'status', '--porcelain']);
    if (dirty) {
      output.push('WARNING: Main directory has uncommitted changes.');
      output.push('   Agents should only work in .worktrees/');
      output.push('');
    }
  }

  // ============================================================
  // Auto-cleanup: Detect merged PRs and cleanup worktrees
  // ============================================================
  const worktreesDir = repoRoot ? path.join(repoRoot, '.worktrees') : null;
  if (worktreesDir && fs.existsSync(worktreesDir)) {
    const worktreeList = execCommand('git', ['-C', repoRoot, 'worktree', 'list', '--porcelain']);
    if (worktreeList) {
      const worktreeLines = worktreeList.split('\n')
        .filter(line => line.startsWith('worktree ') && line.includes('.worktrees/bd-'));

      // Hoist git branch --merged outside the loop (was called per-worktree before)
      const merged = execCommand('git', ['-C', repoRoot, 'branch', '--merged', 'main']);
      const mergedBranches = merged
        ? merged.split('\n').map(b => b.trim().replace(/^\*\s*/, ''))
        : [];

      for (const line of worktreeLines) {
        const wtPath = line.replace('worktree ', '').trim();
        const dirName = path.basename(wtPath);
        const beadId = dirName.replace('bd-', '');

        // Exact match prevents bd-1 matching bd-10
        if (mergedBranches.includes(dirName)) {
          output.push(`ACTION REQUIRED: ${dirName} was merged but bead "${beadId}" is still open.`);
          output.push(`   Run: bd close "${beadId}" && git worktree remove "${wtPath}"`);
          output.push('');
        }
      }
    }
  }

  // ============================================================
  // Open PR Reminder
  // ============================================================
  const openPrs = execCommand('gh', ['pr', 'list', '--author', '@me', '--state', 'open', '--json', 'number,title,headRefName']);
  if (openPrs && openPrs !== '[]') {
    try {
      const prs = JSON.parse(openPrs);
      if (prs.length > 0) {
        output.push('You have open PRs:');
        for (const pr of prs) {
          output.push(`  #${pr.number} ${pr.title} (${pr.headRefName})`);
        }
        output.push('');
      }
    } catch {
      // Skip if gh output can't be parsed
    }
  }

  // bd prime (bd's own SessionStart hook) injects workflow context + beads.
  // Only emit our orchestration extras; stay silent if there is nothing to flag.
  const body = output.join('\n').trim();
  if (body) injectText(body + '\n');
});
