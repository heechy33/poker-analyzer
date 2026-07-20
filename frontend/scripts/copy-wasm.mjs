#!/usr/bin/env node
/**
 * Copy the allowlisted wasm-pack artifacts into `frontend/public/wasm/` and
 * place the AGPL license plus an exact-source notice beside them.
 *
 * Run with `--release` for a distributable bundle. Release mode refuses a
 * dirty repository or an engine checkout that differs from the gitlink.
 */

import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, copyFile, readdir, stat, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..', '..');
const pkgDir = resolve(repoRoot, 'solver-wasm', 'pkg');
const targetDir = resolve(repoRoot, 'frontend', 'public', 'wasm');
const licensePath = resolve(repoRoot, 'LICENSE');

const ARTIFACT_NAMES = new Set([
  'solver_wasm_bg.wasm',
  'solver_wasm.js',
  'solver_wasm.d.ts',
  'solver_wasm_bg.wasm.d.ts',
]);
const REQUIRED_ARTIFACT_NAMES = new Set([
  'solver_wasm_bg.wasm',
  'solver_wasm.js',
  'solver_wasm.d.ts',
]);
const REPOSITORY_URL = 'https://github.com/heechy33/poker-analyzer';

function git(args, cwd = repoRoot) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
}

function sourceOffer({ rootCommit, engineCommit, dirty }) {
  const sourceUrl = `${REPOSITORY_URL}/tree/${rootCommit}`;
  const status = dirty
    ? 'DEVELOPMENT BUILD: the source tree had uncommitted changes. Do not distribute this bundle.'
    : 'Release source: the URL below identifies the exact clean source tree used for this bundle.';
  return `Poker Analyzer solver WASM - AGPL-3.0-or-later source notice

${status}

Corresponding source: ${sourceUrl}
Engine fork commit: https://github.com/heechy33/postflop-solver/tree/${engineCommit}

The corresponding source includes solver-wasm, its build and copy scripts,
and the postflop-solver submodule. Download the source at no charge from the
URL above. The license is included beside this notice as LICENSE.txt.
`;
}

async function main() {
  if (!existsSync(pkgDir)) {
    console.error(`[copy-wasm] expected wasm-pack output at ${pkgDir}`);
    console.error('[copy-wasm] run `npm run build:wasm` first');
    process.exit(1);
  }

  const entries = await readdir(pkgDir);
  const missingArtifacts = [...REQUIRED_ARTIFACT_NAMES].filter(
    (name) => !entries.includes(name),
  );
  if (missingArtifacts.length > 0) {
    throw new Error(`wasm-pack output is missing: ${missingArtifacts.join(', ')}`);
  }

  const rootCommit = git(['rev-parse', 'HEAD']);
  const pinnedEngineCommit = git(['rev-parse', 'HEAD:postflop-solver']);
  const engineCommit = git(
    ['-c', `safe.directory=${resolve(repoRoot, 'postflop-solver')}`, 'rev-parse', 'HEAD'],
    resolve(repoRoot, 'postflop-solver'),
  );
  const dirty = git(['status', '--porcelain', '--untracked-files=no']) !== '';
  const releaseBuild = process.argv.includes('--release');

  if (releaseBuild && dirty) {
    throw new Error('release build requires a clean source tree');
  }
  if (releaseBuild && engineCommit !== pinnedEngineCommit) {
    throw new Error(
      `release engine ${engineCommit} does not match gitlink ${pinnedEngineCommit}`,
    );
  }

  await mkdir(targetDir, { recursive: true });

  let copied = 0;
  for (const name of entries) {
    if (!ARTIFACT_NAMES.has(name)) continue;
    const src = join(pkgDir, name);
    const dst = join(targetDir, name);
    const info = await stat(src);
    if (!info.isFile()) continue;
    await copyFile(src, dst);
    copied += 1;
    console.log(`[copy-wasm] ${name} (${info.size.toLocaleString()} bytes)`);
  }
  if (copied === 0) {
    console.error('[copy-wasm] no allowlisted artifacts found in pkg/');
    process.exit(1);
  }

  await copyFile(licensePath, join(targetDir, 'LICENSE.txt'));
  await writeFile(
    join(targetDir, 'SOURCE-OFFER.txt'),
    sourceOffer({ rootCommit, engineCommit, dirty }),
    'utf8',
  );
  console.log(
    `[copy-wasm] copied ${copied} code artifact(s) plus AGPL notices to ${targetDir}`,
  );
}

main().catch((error) => {
  console.error('[copy-wasm] failed:', error);
  process.exit(1);
});
