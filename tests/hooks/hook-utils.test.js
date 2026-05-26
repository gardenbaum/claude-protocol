import { describe, it, expect } from 'vitest';
import { execFileSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

// hook-utils.cjs exports pure functions we can test directly
const {
  getField,
  parseBeadId,
  parseEpicId,
  containsPathSegment,
  getRepoRoot,
} = require('../../templates/hooks/hook-utils.cjs');

describe('getField', () => {
  it('returns nested value via dot path', () => {
    const obj = { tool_input: { command: 'git status' } };
    expect(getField(obj, 'tool_input.command')).toBe('git status');
  });

  it('returns empty string for missing path', () => {
    expect(getField({ a: 1 }, 'a.b.c')).toBe('');
  });

  it('returns empty string for null input', () => {
    expect(getField(null, 'a')).toBe('');
  });

  it('returns empty string for undefined input', () => {
    expect(getField(undefined, 'a')).toBe('');
  });

  it('returns top-level value', () => {
    expect(getField({ name: 'test' }, 'name')).toBe('test');
  });

  it('returns empty string for null leaf', () => {
    expect(getField({ a: { b: null } }, 'a.b')).toBe('');
  });

  it('returns 0 as-is (not empty string)', () => {
    expect(getField({ count: 0 }, 'count')).toBe(0);
  });

  it('returns false as-is', () => {
    expect(getField({ flag: false }, 'flag')).toBe(false);
  });
});

describe('parseBeadId', () => {
  it('extracts bead ID from text', () => {
    expect(parseBeadId('BEAD_ID: tcp-7uv.1')).toBe('tcp-7uv.1');
  });

  it('handles alphanumeric IDs with dots and dashes', () => {
    expect(parseBeadId('BEAD_ID: BD-001.2')).toBe('BD-001.2');
  });

  it('handles underscores', () => {
    expect(parseBeadId('BEAD_ID: my_bead_1')).toBe('my_bead_1');
  });

  it('returns empty string when no match', () => {
    expect(parseBeadId('no bead here')).toBe('');
  });

  it('returns empty string for null', () => {
    expect(parseBeadId(null)).toBe('');
  });

  it('returns empty string for empty string', () => {
    expect(parseBeadId('')).toBe('');
  });

  it('extracts first match from multiline', () => {
    const text = 'line1\nBEAD_ID: abc-123\nBEAD_ID: def-456';
    expect(parseBeadId(text)).toBe('abc-123');
  });
});

describe('parseEpicId', () => {
  it('extracts epic ID from text', () => {
    expect(parseEpicId('EPIC_ID: tcp-7uv')).toBe('tcp-7uv');
  });

  it('returns empty string when no match', () => {
    expect(parseEpicId('BEAD_ID: abc')).toBe('');
  });

  it('returns empty string for null', () => {
    expect(parseEpicId(null)).toBe('');
  });
});

describe('containsPathSegment', () => {
  it('detects segment in unix path', () => {
    expect(containsPathSegment('/foo/.worktrees/bd-1/bar.ts', '.worktrees')).toBe(true);
  });

  it('detects segment in windows path', () => {
    expect(containsPathSegment('C:\\projects\\.worktrees\\bd-1\\file.js', '.worktrees')).toBe(true);
  });

  it('detects segment at end of path', () => {
    expect(containsPathSegment('/foo/.worktrees', '.worktrees')).toBe(true);
  });

  it('returns false for partial match', () => {
    expect(containsPathSegment('/foo/worktrees-old/file.js', '.worktrees')).toBe(false);
  });

  it('returns false for null path', () => {
    expect(containsPathSegment(null, '.worktrees')).toBe(false);
  });

  it('returns false for empty path', () => {
    expect(containsPathSegment('', '.worktrees')).toBe(false);
  });

  it('detects .claude segment', () => {
    expect(containsPathSegment('/project/.claude/plans/plan.md', '.claude')).toBe(true);
  });
});

describe('getRepoRoot', () => {
  // Helper: create tmp git repo with one commit + a linked worktree.
  // Returns { tmpDir, mainRoot, worktreePath, cleanup }. `mainRoot` is the
  // realpath of tmpDir (macOS /tmp -> /private/tmp symlink resolution).
  function setupRepoWithWorktree() {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cp-getrepo-'));
    const mainRoot = fs.realpathSync(tmpDir);
    const git = (args, cwd) => execFileSync('git', args, { cwd, stdio: 'pipe' });

    git(['init', '-q', '-b', 'main'], mainRoot);
    git(['config', 'user.email', 'test@test'], mainRoot);
    git(['config', 'user.name', 'test'], mainRoot);
    git(['config', 'commit.gpgsign', 'false'], mainRoot);
    git(['commit', '--allow-empty', '-m', 'init', '-q'], mainRoot);

    const worktreePath = path.join(mainRoot, '.worktrees', 'bd-test');
    git(['worktree', 'add', '-q', '-b', 'bd-test', worktreePath, 'HEAD'], mainRoot);

    return {
      mainRoot,
      worktreePath: fs.realpathSync(worktreePath),
      cleanup: () => {
        try { git(['worktree', 'remove', '--force', worktreePath], mainRoot); } catch { /* best effort */ }
        fs.rmSync(mainRoot, { recursive: true, force: true });
      },
    };
  }

  it('returns main repo root when called from main checkout', () => {
    const { mainRoot, cleanup } = setupRepoWithWorktree();
    try {
      expect(fs.realpathSync(getRepoRoot(mainRoot))).toBe(mainRoot);
    } finally {
      cleanup();
    }
  });

  it('returns main repo root when called from inside a linked worktree', () => {
    // Regression: bug from ch_tosca_mailorder-c3a / bd-vn8.1 incident
    // (2026-05-26). 'git rev-parse --show-toplevel' returns the worktree path
    // from inside a linked worktree; downstream code then builds nested
    // '.worktrees/bd-X/.worktrees/bd-X' paths and blocks completion.
    const { mainRoot, worktreePath, cleanup } = setupRepoWithWorktree();
    try {
      expect(fs.realpathSync(getRepoRoot(worktreePath))).toBe(mainRoot);
    } finally {
      cleanup();
    }
  });

  it('returns null outside any git repo', () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cp-nogit-'));
    try {
      expect(getRepoRoot(tmpDir)).toBeNull();
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});
