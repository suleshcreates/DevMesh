// Real browser + real FastAPI/SQLite. No real operator data is mutated.
import { chromium } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const evidencePrefix = process.env.DEVMESH_BROWSER_EVIDENCE_PREFIX || "m4";
const root = path.resolve(import.meta.dirname, "../.."),
  python = path.join(root, ".venv/Scripts/python.exe");
const temp = await mkdtemp(path.join(tmpdir(), "devmesh-m4-browser-"));
const api = "http://127.0.0.1:18420";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let server, browser;
const results = [],
  errors = [];
async function waitFor(test, label) {
  for (let i = 0; i < 150; i++) {
    if (await test().catch(() => false)) return;
    await sleep(100);
  }
  throw new Error("Timed out: " + label);
}
async function start() {
  server = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "backend.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      "18420",
      "--no-access-log",
    ],
    {
      cwd: root,
      windowsHide: true,
      stdio: "ignore",
      env: {
        ...process.env,
        DEVMESH_DATABASE: path.join(temp, "state.sqlite"),
        DEVMESH_EXECUTION_REPOSITORY: temp,
        DEVMESH_TEST_COMMAND: "[]",
      },
    },
  );
  await waitFor(
    async () => (await fetch(api + "/api/dashboard/snapshot")).ok,
    "API startup",
  );
}
async function stop() {
  if (server && !server.killed) {
    const done = new Promise((r) => server.once("exit", r));
    server.kill();
    await done;
  }
}
async function get(p) {
  return (await fetch(api + p)).json();
}
async function post(p, body) {
  const r = await fetch(api + p, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  assert.ok(r.ok, `${p}: ${r.status} ${await r.clone().text()}`);
  return r.json();
}
async function check(name, fn) {
  await fn();
  results.push(name);
  console.log("PASS " + name);
}
try {
  try {
    await fetch(api + "/openapi.json", { signal: AbortSignal.timeout(800) });
    throw new Error(
      "Port 18420 is already occupied; refusing to use or mutate existing API.",
    );
  } catch (e) {
    if (e.message.includes("already occupied")) throw e;
  }
  await start();
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    permissions: ["clipboard-read", "clipboard-write"],
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  let outage = false;
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !outage && !m.text().includes("409"))
      errors.push(m.text());
  });
  // Test-only transport routing to the disposable API. Production URLs stay fixed.
  await page.route("http://127.0.0.1:8420/**", async (route) => {
    try {
      const response = await route.fetch({
        url: route.request().url().replace(":8420", ":18420"),
      });
      await route.fulfill({ response });
    } catch {
      await route.abort("connectionrefused");
    }
  });
  await page.addInitScript(() => {
    const Native = window.WebSocket;
    window.WebSocket = class extends Native {
      constructor(url, protocols) {
        super(
          String(url).replace("127.0.0.1:8420", "127.0.0.1:18420"),
          protocols,
        );
      }
    };
  });
  await page.goto("http://localhost:5173");
  await page.getByText("API connected", { exact: true }).waitFor();
  await check("Honest empty states and unknown heartbeats", async () => {
    assert.equal(await page.getByText("Unknown", { exact: true }).count(), 2);
    await page
      .getByText("No messages recorded. New messages will appear here.")
      .waitFor();
    await page.getByText("Not run", { exact: true }).waitFor();
    assert.equal(
      await page.getByRole("button", { name: "Approve exact action" }).count(),
      0,
    );
  });
  await check("Keyboard focus and mobile viewport", async () => {
    await page.keyboard.press("Tab");
    assert.equal(await page.locator(":focus").innerText(), "Skip to dashboard");
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.evaluate(() => document.activeElement?.blur());
    await page.screenshot({
      path: path.join(root, `docs/${evidencePrefix}-mobile.png`),
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
  });
  await check(
    "Clipboard contains supported bounded bridge instructions",
    async () => {
      await page
        .getByLabel("Project goal · transient draft")
        .fill("Inspect current records");
      await page
        .getByRole("button", { name: "Copy Antigravity instructions" })
        .click();
      await page.getByText("Instructions copied to clipboard.").waitFor();
      const text = await page.evaluate(() => navigator.clipboard.readText());
      assert.ok(text.includes("await_next_task"));
      assert.ok(text.includes("8420"));
      assert.ok(!text.includes("8421"));
    },
  );
  const message = await post("/api/messages", {
    sender: "codex",
    recipient: "antigravity",
    subject: "Browser persisted message",
    content:
      "**Recorded Markdown**\n```diff\n+ safe line\n```\n<script>window.injected=true</script>",
    type: "proposal",
  });
  await post("/api/agents/codex/heartbeat", {});
  let d = await post("/api/decisions/propose", {
    request_id: crypto.randomUUID(),
    agent: "codex",
    title: "Browser fixture decision",
    content: "Proposal A: local storage",
    rationale: "Durability",
  });
  for (const agent of ["antigravity", "codex", "antigravity"])
    d = await post(`/api/decisions/${d.id}/critique`, {
      request_id: crypto.randomUUID(),
      agent,
      expected_revision: d.revision,
      critique_json: JSON.stringify({
        verdict: "CHALLENGE",
        identified_risks: [
          { description: "Restore must be verified", blocking: true },
        ],
        counter_proposal: `Counter-proposal ${d.revision}`,
        tradeoff_analysis: {
          security: "Local boundary",
          deployment: "One process",
          complexity: "Explicit restore checks",
        },
      }),
    });
  const task = await post("/api/tasks", {
    request_id: crypto.randomUUID(),
    title: "Browser fixture task",
    description: "Read-only board check",
    touched_files_scope: ["example.txt"],
    acceptance_criteria: ["Recorded acceptance criterion"],
  });
  const action = {
    kind: "command",
    arguments: ["python", "-c", 'print("fixture only")'],
    repository: temp,
    worktree: temp,
    scope: ["example.txt"],
    target: null,
    context: { purpose: "Disposable browser UI verification; never executed" },
  };
  const approval = await post("/api/approvals", action);
  await page.getByRole("button", { name: "Refresh state" }).click();
  await check(
    "Persisted message, safe Markdown, heartbeat, decision, task and exact action render",
    async () => {
      await page
        .getByText("Browser persisted message", { exact: true })
        .waitFor();
      await page.getByText("Recent heartbeat", { exact: true }).waitFor();
      await page.getByText("Browser fixture task", { exact: true }).waitFor();
      const msg = page
        .locator(".timeline article")
        .filter({ hasText: "Browser persisted message" });
      await msg.getByText("Read message / Markdown", { exact: true }).click();
      await msg.getByText("Recorded Markdown", { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.injected), undefined);
      assert.equal(
        (await get("/api/inbox/antigravity")).messages.find(
          (m) => m.id === message.id,
        ).delivered,
        false,
      );
      const approvals = page.locator("#approvals");
      await approvals
        .getByRole("button", { name: "Approve proposal 4" })
        .waitFor();
      await approvals
        .locator("summary")
        .filter({ hasText: approval.id.slice(0, 8) })
        .click();
      await approvals
        .getByText(approval.fingerprint, { exact: true })
        .waitFor();
      assert.ok(
        (await approvals.locator("pre").allTextContents()).some((v) =>
          v.includes(temp.replaceAll("\\", "\\\\")),
        ),
      );
      await page.evaluate(() => {
        document.activeElement?.blur();
        window.scrollTo(0, 0);
      });
      await page.screenshot({ path: path.join(root, `docs/${evidencePrefix}-desktop.png`) });
    },
  );
  await check(
    "Explicit exact-action decision binds request and fingerprint; no execution",
    async () => {
      const record = page
        .locator("#approvals details")
        .filter({ hasText: approval.id });
      await record
        .getByLabel("Reason for approval or denial")
        .fill("Approve disposable UI fixture only; do not execute");
      const capture = page.waitForRequest((r) =>
        r.url().endsWith(`/api/approvals/${approval.id}/decision`),
      );
      await record
        .getByRole("button", { name: "Approve exact action" })
        .click();
      assert.equal(
        (await capture).postDataJSON().fingerprint,
        approval.fingerprint,
      );
      await record
        .getByText("This request is approved; no further decision is offered.")
        .waitFor();
      assert.ok(
        !(await get("/api/audit-logs")).audit_logs.some(
          (a) => a.event === "execution_attempt",
        ),
      );
    },
  );
  await check(
    "Human resolution selects actual fourth proposal and records human_resolved",
    async () => {
      const record = page
        .locator("#approvals article")
        .filter({ hasText: "Browser fixture decision" });
      await record
        .getByLabel("Resolution rationale")
        .fill("Select the recorded fourth proposal in isolated data");
      await record.getByRole("button", { name: "Approve proposal 4" }).click();
      await waitFor(
        async () =>
          (await get(`/api/decisions/${d.id}`)).status === "human_resolved",
        "human resolution",
      );
      const final = await get(`/api/decisions/${d.id}`);
      assert.equal(final.resolution, d.proposals[3].content);
      assert.equal(final.status, "human_resolved");
    },
  );
  await check(
    "Failed human submission preserves text and retries identical request ID only",
    async () => {
      let failed = await post("/api/decisions/propose", {
        request_id: crypto.randomUUID(),
        agent: "codex",
        title: "Recoverable network fixture",
        content: "Original directive",
        rationale: "Test recovery",
      });
      for (const agent of ["antigravity", "antigravity"])
        failed = await post(`/api/decisions/${failed.id}/critique`, {
          request_id: crypto.randomUUID(),
          agent,
          expected_revision: failed.revision,
          critique_json: "invalid JSON",
        });
      await page.getByRole("button", { name: "Refresh state" }).click();
      const record = page
        .locator("#approvals article")
        .filter({ hasText: "Recoverable network fixture" });
      await record
        .getByLabel("Resolution rationale")
        .fill("Preserve this rationale");
      await record
        .getByLabel("Custom directive")
        .fill("Preserve this custom directive");
      const payloads = [];
      const intercept = async (route) => {
        payloads.push(route.request().postDataJSON());
        if (payloads.length === 1) await route.abort("connectionrefused");
        else await route.fallback();
      };
      await page.route(
        `**/api/decisions/${failed.id}/human-resolve`,
        intercept,
      );
      outage = true;
      await record
        .getByRole("button", { name: "Record custom human resolution" })
        .click();
      await record
        .getByRole("button", { name: "I have reviewed the refreshed decision" })
        .waitFor();
      assert.equal(
        await record.getByLabel("Custom directive").inputValue(),
        "Preserve this custom directive",
      );
      assert.equal(
        await record.getByLabel("Resolution rationale").inputValue(),
        "Preserve this rationale",
      );
      await record
        .getByRole("button", { name: "I have reviewed the refreshed decision" })
        .click();
      await record
        .getByRole("button", { name: "Record custom human resolution" })
        .click();
      await waitFor(
        async () =>
          (await get(`/api/decisions/${failed.id}`)).status ===
          "human_resolved",
        "recovered directive",
      );
      assert.equal(payloads.length, 2);
      assert.deepEqual(payloads[0], payloads[1]);
      await page.unroute(
        `**/api/decisions/${failed.id}/human-resolve`,
        intercept,
      );
      outage = false;
    },
  );
  await check(
    "Denied and consumed requests cannot be reused; persisted integration log is visible",
    async () => {
      const denied = await post("/api/approvals", action),
        consumed = await post("/api/approvals", action);
      // Fixtures only: mark a synthetic request consumed and insert a recorded result without executing anything.
      execFileSync(
        python,
        [
          "-c",
          "import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE approval_requests SET state=? WHERE id=?',('consumed',sys.argv[2])); c.execute('INSERT INTO integration_attempts VALUES (?,?,?,?)',('browser-result',sys.argv[3],'finished',json.dumps({'promoted':False,'test_log':'Disposable recorded assertion failure'}))); c.commit()",
          path.join(temp, "state.sqlite"),
          consumed.id,
          task.id,
        ],
        { windowsHide: true },
      );
      await page.getByRole("button", { name: "Refresh state" }).click();
      const center = page.locator("#approvals");
      await center
        .locator("summary")
        .filter({ hasText: denied.id.slice(0, 8) })
        .click();
      const deny = page
        .locator("#approvals details")
        .filter({ hasText: denied.id });
      await deny
        .getByLabel("Reason for approval or denial")
        .fill("Deny disposable action");
      await deny.getByRole("button", { name: "Deny exact action" }).click();
      await deny
        .getByText("This request is denied; no further decision is offered.")
        .waitFor();
      await center
        .locator("summary")
        .filter({ hasText: consumed.id.slice(0, 8) })
        .click();
      const used = page
        .locator("#approvals details")
        .filter({ hasText: consumed.id });
      await used
        .getByText("Consumed · this approval cannot be reused.")
        .waitFor();
      assert.equal(await used.getByRole("button").count(), 0);
      const card = page
        .locator(".task")
        .filter({ hasText: "Browser fixture task" });
      await card.getByText("Task details", { exact: true }).click();
      await card
        .getByText("Disposable recorded assertion failure", { exact: true })
        .waitFor();
      await card
        .getByText("Recorded acceptance criterion", { exact: true })
        .waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.setViewportSize({ width: 1440, height: 1000 });
    },
  );
  await check(
    "API disconnected state and WebSocket reconnect rehydrate persisted state",
    async () => {
      outage = true;
      await stop();
      await page.getByRole("button", { name: "Refresh state" }).click();
      await page.getByText("API disconnected", { exact: true }).waitFor();
      await page
        .getByText("Dashboard socket: Reconnecting", { exact: true })
        .waitFor();
      await start();
      await post("/api/messages", {
        sender: "antigravity",
        recipient: "codex",
        subject: "Persisted during reconnect",
        content: "REST snapshot must recover this record",
      });
      await page
        .getByText("Persisted during reconnect", { exact: true })
        .waitFor({ timeout: 35000 });
      await page.getByText("API connected", { exact: true }).waitFor();
      await page
        .getByText("Dashboard socket: Connected", { exact: true })
        .waitFor();
      outage = false;
      assert.equal(
        await page
          .getByText("Browser persisted message", { exact: true })
          .count(),
        1,
      );
    },
  );
  await check("No unexpected browser console errors", async () =>
    assert.deepEqual(errors, []),
  );
  await writeFile(
    path.join(root, `docs/${evidencePrefix}-browser-results.json`),
    JSON.stringify(
      {
        browser: "Microsoft Edge via Playwright, headless",
        fixture_directory: temp,
        results,
        unexpected_errors: errors,
      },
      null,
      2,
    ) + "\n",
  );
} finally {
  await browser?.close();
  await stop();
}
