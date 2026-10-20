import { useRef, useState } from "react";
import { request } from "./api";
import type { Project } from "./contracts";

export function ProjectControls({
  project,
  disabled,
  refresh,
}: {
  project?: Project;
  disabled: boolean;
  refresh: () => Promise<void>;
}) {
  const [goal, setGoal] = useState(""),
    [repository, setRepository] = useState("");
  const [command, setCommand] = useState(""),
    [reason, setReason] = useState("");
  const [manual, setManual] = useState(false),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(""),
    [clipboard, setClipboard] = useState("");
  const prior = useRef<{ fingerprint: string; id: string } | null>(null);
  const [reviewedRevision, setReviewedRevision] = useState<number | null>(null);
  async function start() {
    setBusy(true);
    setError("");
    try {
      const parsed: unknown = JSON.parse(command);
      if (
        !Array.isArray(parsed) ||
        !parsed.length ||
        parsed.some((x) => typeof x !== "string" || !x.trim())
      )
        throw new Error("Enter a JSON argument array for the test command.");
      const payload = {
        goal,
        repository,
        test_command: parsed,
        execution_mode: "manual",
      };
      const fingerprint = JSON.stringify(payload);
      if (prior.current?.fingerprint !== fingerprint)
        prior.current = { fingerprint, id: crypto.randomUUID() };
      await request("/api/projects/start", {
        ...payload,
        request_id: prior.current.id,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Project start failed");
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  async function control(action: string) {
    if (!project) return;
    setBusy(true);
    setError("");
    try {
      await request(`/api/projects/${project.id}/${action}`, {
        request_id: crypto.randomUUID(),
        expected_revision: project.revision,
        reason,
        manual_handoff: manual,
      });
      setReviewedRevision(null);
      setManual(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
      setReviewedRevision(null);
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  async function copy() {
    try {
      const prompt = project
        ? (
            await request<{ prompt: string }>(
              `/api/projects/${project.id}/activation`,
            )
          ).prompt
        : "Use devmesh-bridge at API http://127.0.0.1:8420. Call read_agent_inbox with agent_name antigravity. If empty, call await_next_task with agent_name antigravity and timeout_seconds 45 once. Do not acknowledge solely for reading; do not execute tasks or approvals. Start a project in the dashboard for project-specific activation.";
      await navigator.clipboard.writeText(prompt);
      setClipboard("Instructions copied to clipboard.");
    } catch {
      setClipboard("Clipboard failed. Allow clipboard access and try again.");
    }
  }
  return (
    <div>
      {!project ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void start();
          }}
        >
          <label>
            Project goal · transient draft
            <textarea
              placeholder="Describe the goal you want to discuss…"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              required
            />
          </label>
          <label>
            Execution repository
            <input
              value={repository}
              onChange={(e) => setRepository(e.target.value)}
              placeholder="Explicit external Git repository path"
              required
            />
          </label>
          <label>
            Test command · JSON arguments
            <textarea
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder={'["python", "-m", "pytest", "-q"]'}
              required
            />
          </label>
          <p className="muted">
            Start persists this goal and configuration. One project per
            database. Select a clean external repository on an operator branch
            at main, with /.worktrees/ ignored. Production task execution
            requires a manual handoff.
          </p>
          <button
            disabled={
              disabled ||
              busy ||
              !goal.trim() ||
              !repository.trim() ||
              !command.trim()
            }
          >
            Start project
          </button>
        </form>
      ) : (
        <div className="project-state" aria-live="polite">
          <h3>{project.goal}</h3>
          <p>
            Project state: <strong>{project.state}</strong> · revision{" "}
            {project.revision}
          </p>
          <p>
            {project.progress.complete}/{project.progress.required} required
            tasks complete
          </p>
          <p>
            {project.reason ||
              "Waiting for the next recorded lifecycle transition."}
          </p>
          <p>
            Repository: <code>{project.repository}</code>
          </p>
          <p>
            Execution mode: {project.execution_mode} · memory:{" "}
            <code>{project.memory?.version || "Not locked yet"}</code>
          </p>
          <p>
            Pending activation / responses:{" "}
            {project.jobs
              .filter((j) => j.status === "pending" || j.status === "running")
              .map((j) => `${j.agent}: ${j.phase} (${j.status})`)
              .join(", ") || "None"}
          </p>
          <p>
            Limits: 3 critique rounds · 3 failed attempts per task · 40
            observable tools per task · 600 seconds per execution attempt.
            Independent IDE tools may be unobservable.
          </p>
          <details>
            <summary>
              Assignments, execution attempts and memory versions
            </summary>
            <pre>
              {JSON.stringify(
                {
                  tasks: project.tasks,
                  attempts: project.attempts,
                  jobs: project.jobs.map((j) => ({
                    id: j.id,
                    agent: j.agent,
                    phase: j.phase,
                    task_id: j.task_id,
                    commit: j.commit,
                    status: j.status,
                    context: j.phase === "execution" ? j.context : undefined,
                  })),
                },
                null,
                2,
              )}
            </pre>
          </details>
          {!["complete", "cancelled"].includes(project.state) && (
            <div>
              <label>
                Project control reason
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                disabled={disabled || busy}
                onClick={() => setReviewedRevision(project.revision)}
              >
                Review current project revision
              </button>
              <label>
                <input
                  type="checkbox"
                  checked={manual}
                  onChange={(e) => setManual(e.target.checked)}
                />
                I inspected the assigned worktree and scope and will perform
                execution manually.
              </label>
              <button
                disabled={
                  disabled ||
                  busy ||
                  !reason.trim() ||
                  reviewedRevision !== project.revision
                }
                onClick={() =>
                  void control(project.state === "paused" ? "resume" : "pause")
                }
              >
                {project.state === "paused"
                  ? "Resume project"
                  : "Pause project"}
              </button>
              <button
                disabled={
                  disabled ||
                  busy ||
                  !reason.trim() ||
                  reviewedRevision !== project.revision
                }
                onClick={() => void control("cancel")}
              >
                Cancel project
              </button>
              {reviewedRevision !== null &&
                reviewedRevision !== project.revision && (
                  <p>Project changed; review the current revision again.</p>
                )}
            </div>
          )}
        </div>
      )}
      <button disabled={disabled || busy} onClick={() => void copy()}>
        Copy Antigravity instructions ↗
      </button>
      <p role="status">{clipboard}</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
