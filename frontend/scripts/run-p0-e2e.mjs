import { spawn } from "node:child_process";

const appUrl = "http://127.0.0.1:3100";
const authUrl = "http://127.0.0.1:54321";
const acceptanceEnv = {
  ...process.env,
  NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
  NEXT_PUBLIC_SUPABASE_ANON_KEY: "p0-acceptance-anon-key",
  NEXT_PUBLIC_SUPABASE_URL: authUrl,
};

const children = [];

function start(args) {
  const child = spawn(process.execPath, args, {
    cwd: process.cwd(),
    env: acceptanceEnv,
    stdio: "inherit",
  });
  children.push(child);
  return child;
}

async function waitUntilReady(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { redirect: "follow" });
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  await waitForExit(child, 2_000);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await waitForExit(child, 2_000);
  }
}

async function cleanup() {
  await Promise.all(children.map(stop));
}

let exitCode = 1;
try {
  start(["e2e/mock-supabase.mjs"]);
  start([
    "node_modules/next/dist/bin/next",
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    "3100",
  ]);

  await Promise.all([
    waitUntilReady(`${authUrl}/health`, 30_000),
    waitUntilReady(`${appUrl}/login`, 120_000),
  ]);

  const playwright = start(["node_modules/@playwright/test/cli.js", "test"]);
  exitCode = await new Promise((resolve) => {
    playwright.once("exit", (code) => resolve(code ?? 1));
  });
} finally {
  await cleanup();
}

process.exitCode = exitCode;
