// Real Edge, frontend, API, SQLite and disposable Git gates. Inference uses injected fixtures.
import { chromium } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
import net from "node:net";
const root = path.resolve(import.meta.dirname, "../..");
const temp = await mkdtemp(path.join(tmpdir(), "devmesh-m5-browser-"));
const api = "http://127.0.0.1:18425";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let server, browser;
const results = [],
  errors = [];
async function waitFor(fn, label, iterations = 400) {
  for (let i = 0; i < iterations; i++) {
    if (await fn().catch(() => false)) return;
    await sleep(100);
  }
  throw new Error("Timed out: " + label);
}
async function check(name, fn) {
  await fn();
  results.push(name);
  console.log("PASS " + name);
}
async function get(p) {
  const r = await fetch(api + p);
  assert.ok(r.ok);
  return r.json();
}
async function free(port) {
  await new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once("error", reject);
    s.listen(port, "127.0.0.1", () => s.close(resolve));
  });
}
async function start() {
  server = spawn(
    path.join(root, ".venv/Scripts/python.exe"),
    ["frontend/e2e/m5_fixture.py", "--directory", temp, "--port", "18425"],
    { cwd: root, windowsHide: true, stdio: "ignore" },
  );
  await waitFor(
    async () => (await fetch(api + "/api/projects")).ok,
    "fixture API",
  );
}
async function stop() {
  if (server && server.exitCode === null) {
    const done = new Promise((r) => server.once("exit", r));
    server.kill();
    await done;
  }
}
try {
  await free(18425);
  await start();
  const fixture = JSON.parse(
    await readFile(path.join(temp, "fixture.json"), "utf8"),
  );
  const mainBefore = execFileSync(
    "git",
    ["-C", fixture.repository, "rev-parse", "main"],
    { encoding: "utf8" },
  ).trim();
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("http://127.0.0.1:8420/**", async (route) => {
    try {
      const response = await route.fetch({
        url: route.request().url().replace(":8420", ":18425"),
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
          String(url).replace("127.0.0.1:8420", "127.0.0.1:18425"),
          protocols,
        );
      }
    };
  });
  await page.goto("http://localhost:5173");
  await page.getByText("API connected", { exact: true }).waitFor();
  await check(
    "Goal entry starts real persisted project and reaches actual escalation",
    async () => {
      await page
        .getByLabel("Project goal · transient draft")
        .fill("build a small login feature");
      await page
        .getByLabel("Execution repository", { exact: true })
        .fill(fixture.repository);
      await page
        .getByLabel("Test command · JSON arguments")
        .fill(JSON.stringify(fixture.test_command));
      await page
        .getByRole("button", { name: "Start project", exact: true })
        .click();
      await waitFor(
        async () =>
          (await get("/api/projects")).projects[0]?.state === "paused",
        "escalated project",
      );
      await page
        .getByRole("button", { name: "Refresh state", exact: true })
        .click();
      await page
        .locator(".project-state")
        .getByText("paused", { exact: true })
        .waitFor();
      const p = (await get("/api/projects")).projects[0];
      const d = await get("/api/decisions/" + p.decision_id);
      assert.equal(d.round_count, 3);
      assert.equal(d.state, "ESCALATE");
      assert.equal(
        execFileSync("git", ["-C", fixture.repository, "rev-parse", "main"], {
          encoding: "utf8",
        }).trim(),
        mainBefore,
      );
    },
  );
  await check(
    "Current-project activation clipboard names durable queue and reactivation",
    async () => {
      await page
        .getByRole("button", { name: "Copy Antigravity instructions" })
        .click();
      await page.getByText("Instructions copied to clipboard.").waitFor();
      const prompt = await page.evaluate(() => navigator.clipboard.readText());
      assert.ok(prompt.includes((await get("/api/projects")).projects[0].id));
      assert.ok(
        prompt.includes("await_next_task") &&
          prompt.includes("re-paste") &&
          prompt.includes("manual handoff"),
      );
    },
  );
  await check(
    "Paused state survives actual API stop/restart and browser reconnect",
    async () => {
      const prior = (await get("/api/projects")).projects[0];
      await stop();
      await page
        .getByText("API disconnected", { exact: true })
        .waitFor({ timeout: 20000 });
      assert.ok(
        await page
          .getByRole("button", { name: "Resume project", exact: true })
          .isDisabled(),
      );
      assert.ok(
        (await page.locator(".project-state").innerText()).includes("paused"),
      );
      await start();
      await page
        .getByText("API connected", { exact: true })
        .waitFor({ timeout: 40000 });
      const next = (await get("/api/projects")).projects[0];
      assert.equal(next.id, prior.id);
      assert.equal(next.state, "paused");
      assert.deepEqual(next.analyses, prior.analyses);
    },
  );
  await check(
    "Human resolution leaves project paused until explicit revision-bound resume",
    async () => {
      await page
        .getByLabel("Resolution rationale")
        .fill(
          "Operator selects bounded fixture design after inspecting three rounds",
        );
      await page
        .getByLabel("Custom directive")
        .fill("Build the small login with validation and a form");
      await page
        .getByRole("button", {
          name: "Record custom human resolution",
          exact: true,
        })
        .click();
      await waitFor(
        async () =>
          (
            await get(
              "/api/decisions/" +
                (await get("/api/projects")).projects[0].decision_id,
            )
          ).state === "RECORD",
        "human resolution",
      );
      assert.equal((await get("/api/projects")).projects[0].state, "paused");
      await page
        .getByLabel("Project control reason")
        .fill("Inspected the resolved decision and current project revision");
      await page
        .getByRole("button", {
          name: "Review current project revision",
          exact: true,
        })
        .click();
      await page
        .getByRole("button", { name: "Resume project", exact: true })
        .click();
    },
  );
  for (let index = 0; index < 2; index++) {
    await check(
      `Task ${index + 1} waits for exact operator approval then executes real tests`,
      async () => {
        await waitFor(
          async () =>
            (await get("/api/projects")).projects[0].state === "approval",
          "pending exact action",
          900,
        );
        const p = (await get("/api/projects")).projects[0];
        const a = (await get("/api/approvals")).requests.find(
          (a) => a.id === p.integration.approval_id,
        );
        assert.equal(a.state, "pending");
        assert.equal(p.progress.complete, index);
        const t = (await get("/api/tasks")).tasks.find(
          (t) => t.id === a.document.context.task_id,
        );
        assert.equal(t.review.commit, a.document.context.reviewed_commit);
        assert.notEqual(t.review.reviewer, t.assigned_agent);
        if (index === 0)
          assert.equal(
            execFileSync(
              "git",
              ["-C", fixture.repository, "rev-parse", "main"],
              { encoding: "utf8" },
            ).trim(),
            mainBefore,
          );
        await page
          .getByRole("button", { name: "Refresh state", exact: true })
          .click();
        const record = page
          .locator("#approvals details.record")
          .filter({ hasText: a.id });
        await record.locator("summary").click();
        await record
          .getByLabel("Reason for approval or denial")
          .fill(
            "Operator inspected this exact disposable unittest action and reviewed commit",
          );
        const sent = page.waitForRequest((r) =>
          r.url().includes(`/api/approvals/${a.id}/decision`),
        );
        await record
          .getByRole("button", { name: "Approve exact action", exact: true })
          .click();
        assert.equal((await sent).postDataJSON().fingerprint, a.fingerprint);
        await waitFor(
          async () =>
            (await get("/api/tasks")).tasks.find((x) => x.id === t.id)
              .status === "complete",
          "tested task promotion",
          900,
        );
      },
    );
  }
  await check(
    "PROJECT_COMPLETE reflects both actual guarded promotions and test logs",
    async () => {
      await waitFor(
        async () =>
          (await get("/api/projects")).projects[0].state === "complete",
        "project completion",
      );
      await page
        .getByRole("button", { name: "Refresh state", exact: true })
        .click();
      await page
        .locator(".project-state")
        .getByText("complete", { exact: true })
        .waitFor();
      assert.ok(
        (await page.locator(".project-state").innerText()).includes(
          "2/2 required tasks complete",
        ),
      );
      const snapshot = await get("/api/dashboard/snapshot");
      assert.equal(snapshot.integration_attempts.length, 2);
      assert.ok(
        snapshot.integration_attempts.every(
          (a) =>
            a.state === "finished" &&
            a.document.promoted &&
            a.document.test_log.includes("OK"),
        ),
      );
      assert.ok(
        snapshot.messages.some((m) => m.subject === "PROJECT_COMPLETE"),
      );
      await page.screenshot({ path: path.join(root, "docs/m5-overview.png"), fullPage: false });
      await page.screenshot({
        path: path.join(root, "docs/m5-complete.png"),
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.screenshot({
        path: path.join(root, "docs/m5-mobile.png"),
        fullPage: true,
      });
    },
  );
  assert.deepEqual(errors, []);
  await writeFile(
    path.join(root, "docs/m5-browser-results.json"),
    JSON.stringify(
      {
        results,
        page_errors: errors,
        boundary:
          "Injected fake inference; real frontend, API, database, fixture commits, unittest commands and guarded promotion. No real-agent pass.",
        fixture_directory: temp,
      },
      null,
      2,
    ) + "\n",
  );
  console.log(
    JSON.stringify({ passed: results.length, fixture_directory: temp }),
  );
} finally {
  if (browser) await browser.close();
  await stop();
}
