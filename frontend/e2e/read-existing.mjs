// Render an online SQLite backup; never mutate the original operator database.
import { chromium } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const root = path.resolve(import.meta.dirname, "../.."),
  python = path.join(root, ".venv/Scripts/python.exe");
const directory = await mkdtemp(path.join(tmpdir(), "devmesh-m4-existing-")),
  database = path.join(directory, "copy.sqlite");
execFileSync(
  python,
  [
    "-c",
    "import sqlite3,sys,pathlib; source=sqlite3.connect(pathlib.Path(sys.argv[1]).as_uri()+'?mode=ro',uri=True); target=sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()",
    path.join(root, ".devmesh/devmesh.sqlite3"),
    database,
  ],
  { windowsHide: true },
);
let server, browser;
try {
  let occupied = false;
  try {
    await fetch("http://127.0.0.1:18422/openapi.json", { signal: AbortSignal.timeout(800) });
    occupied = true;
  } catch {}
  assert.equal(occupied, false, "Port 18422 is occupied; refusing to attach to an existing API");
  server = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "backend.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      "18422",
      "--no-access-log",
    ],
    {
      cwd: root,
      windowsHide: true,
      stdio: "ignore",
      env: {
        ...process.env,
        DEVMESH_DATABASE: database,
        DEVMESH_EXECUTION_REPOSITORY: directory,
        DEVMESH_TEST_COMMAND: "[]",
      },
    },
  );
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try {
      ready = (await fetch("http://127.0.0.1:18422/api/dashboard/snapshot")).ok;
    } catch {}
    if (ready) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.ok(ready);
  const snapshot = await (
    await fetch("http://127.0.0.1:18422/api/dashboard/snapshot")
  ).json();
  const decisions = await (
    await fetch("http://127.0.0.1:18422/api/decisions?open_only=false")
  ).json();
  const live = await (
    await fetch("http://127.0.0.1:8420/api/decisions?open_only=false")
  ).json();
  assert.deepEqual(decisions, live);
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
      reducedMotion: "reduce",
    }),
    errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.route("http://127.0.0.1:8420/**", async (route) => {
    const response = await route.fetch({
      url: route.request().url().replace(":8420", ":18422"),
    });
    await route.fulfill({ response });
  });
  await page.addInitScript(() => {
    const Native = window.WebSocket;
    window.WebSocket = class extends Native {
      constructor(url, protocols) {
        super(
          String(url).replace("127.0.0.1:8420", "127.0.0.1:18422"),
          protocols,
        );
      }
    };
  });
  await page.goto("http://localhost:5173");
  await page.getByText("API connected", { exact: true }).waitFor();
  assert.equal(
    await page.locator(".timeline>li").count(),
    snapshot.messages.length,
  );
  assert.equal(
    await page.locator("#decisions>.record").count(),
    decisions.decisions.length,
  );
  for (const d of decisions.decisions) {
    const record = page.locator("#decisions>.record").filter({ hasText: d.id });
    await record.locator("summary").first().click();
    await record.getByText(d.status, { exact: true }).first().waitFor();
  }
  assert.deepEqual(errors, []);
  await page.screenshot({
    path: path.join(root, "docs/m4-existing-records.png"),
  });
  const result = {
    verification:
      "Real browser rendered an isolated online backup of existing SQLite records; copied decisions exactly matched live read-only REST response.",
    messages: snapshot.messages.length,
    decisions: decisions.decisions.map((d) => ({ id: d.id, status: d.status })),
    unexpected_errors: errors,
    original_mutations: 0,
  };
  await writeFile(
    path.join(root, "docs/m4-existing-results.json"),
    JSON.stringify(result, null, 2) + "\n",
  );
  console.log(JSON.stringify(result));
} finally {
  await browser?.close();
  if (server && !server.killed) {
    const done = new Promise((r) => server.once("exit", r));
    server.kill();
    await done;
  }
}
