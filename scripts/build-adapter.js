#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// Build script: transpiles the OpenCode plugin (TS source) to plain JS that
// OpenCode can `bun install` and load. We deliberately avoid a build
// dependency by hand-rolling a small TS->JS conversion — the plugin is
// self-contained and the source is small enough for this to be cheaper than
// pulling in tsc/esbuild/tsx.
//
// Run via: node scripts/build-adapter.js opencode|omp
// (currently no-op: we ship pre-transpiled .js alongside the .ts source.

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");

const targets = {
  opencode: {
    src: "templates/opencode/plugins/claude-protocol.ts",
    out: "templates/opencode/plugins/claude-protocol.js",
  },
  omp: {
    src: "templates/omp/extensions/claude-protocol.ts",
    out: "templates/omp/extensions/claude-protocol.js",
  },
};

async function bundle(target) {
  if (!target) {
    for (const name of Object.keys(targets)) await bundle(name);
    return;
  }
  const { src, out } = targets[target];
  const srcPath = path.join(ROOT, src);
  if (!existsSync(srcPath)) return;
  await mkdir(path.dirname(path.join(ROOT, out)), { recursive: true });
  await writeFile(path.join(ROOT, out), await readFile(srcPath, "utf8"), "utf8");
}

const name = process.argv[2];
if (name && !targets[name]) {
  console.error(`Unknown adapter: ${name}. Known: ${Object.keys(targets).join(", ")}`);
  process.exit(2);
}
await bundle(name);
