import { describe, it, expect } from "vitest";
import { heartbeat, uniqueMessages, testGate, type Message } from "./contracts";
describe("Honest persisted dashboard state", () => {
  it("distinguishes unknown, recent, stale and offline heartbeats", () => {
    const now = Date.parse("2026-10-20T00:10:00Z");
    expect(heartbeat(undefined, now)).toBe("Unknown");
    expect(heartbeat("invalid", now)).toBe("Unknown");
    expect(heartbeat("2026-10-20T00:09:00Z", now)).toBe("Recent heartbeat");
    expect(heartbeat("2026-10-20T00:08:59Z", now)).toBe("Stale");
    expect(heartbeat("2026-10-20T00:06:59Z", now)).toBe("Offline");
  });
  it("deduplicates by persisted ID and reconciles acknowledgement", () => {
    const message = {
      id: "one",
      created_at: "2026-10-20",
      delivered: false,
    } as Message;
    expect(uniqueMessages([message, { ...message, delivered: true }])).toEqual([
      { ...message, delivered: true },
    ]);
  });
  it("does not infer passing tests from absent or unfinished attempts", () => {
    expect(testGate([])).toBe("Not run");
    expect(
      testGate([{ id: "a", task_id: "t", state: "running", document: {} }]),
    ).toBe("1 unknown");
    expect(
      testGate([
        {
          id: "a",
          task_id: "t",
          state: "finished",
          document: { promoted: false },
        },
      ]),
    ).toBe("0/1 promoted");
  });
});
