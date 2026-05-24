#!/usr/bin/env node
/**
 * Copy the wasm-pack artifacts from `solver-wasm/pkg/` into
 * `frontend/public/wasm/` so Next.js can serve them statically.
 *
 * Run via `npm run build:wasm` (which invokes `wasm-pack` first).
 *
 * The set of files we ship is deliberately small:
 *   - <crate>_bg.wasm   - the compiled module
 *   - <crate>.js        - the JS loader / bindings
 *   - <crate>.d.ts      - the TypeScript declarations
 *   - <crate>_bg.wasm.d.ts (optional)
 *
 * `package.json`, `README.md`, and the LICENSE files emitted by wasm-pack
 * are NOT copied — they live inside `solver-wasm/pkg/` for npm-style
 * publishing, which we do not use.
 */

import { mkdir, copyFile, readdir, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..', '..');
const pkgDir = resolve(repoRoot, 'solver-wasm', 'pkg');
const targetDir = resolve(repoRoot, 'frontend', 'public', 'wasm');

const ALLOWED_EXTENSIONS = ['.wasm', '.js', '.d.ts', '.cjs', '.mjs'];

async function main() {
  if (!existsSync(pkgDir)) {
    console.error(`[copy-wasm] expected wasm-pack output at ${pkgDir}`);
    console.error(`[copy-wasm] run \`wasm-pack build solver-wasm --target web --release\` first`);
    process.exit(1);
  }

  await mkdir(targetDir, { recursive: true });

  const entries = await readdir(pkgDir);
  let copied = 0;
  for (const name of entries) {
    if (!ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext))) continue;
    const src = join(pkgDir, name);
    const dst = join(targetDir, name);
    const info = await stat(src);
    if (!info.isFile()) continue;
    await copyFile(src, dst);
    copied += 1;
    console.log(`[copy-wasm] ${name} (${info.size.toLocaleString()} bytes)`);
  }
  if (copied === 0) {
    console.error('[copy-wasm] no matching artifacts found in pkg/');
    process.exit(1);
  }
  console.log(`[copy-wasm] copied ${copied} file(s) to ${targetDir}`);
}

main().catch((err) => {
  console.error('[copy-wasm] failed:', err);
  process.exit(1);
});
