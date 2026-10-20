import { useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import { Network } from "./Network";
import { ProjectControls } from "./ProjectControls";
import { useDashboard, request } from "./api";
import {
  heartbeat,
  testGate,
  type Decision,
  type Approval,
  type Data,
} from "./contracts";
import "./style.css";
function Badge({
  children,
  agent = "",
}: {
  children: ReactNode;
  agent?: string;
}) {
  return <span className={`badge ${agent}`}>{children}</span>;
}
function Json({ value }: { value: unknown }) {
  return (
    <pre>
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}
function Prose({ text }: { text: string }) {
  return (
    <div className="prose">
      <Markdown
        skipHtml
        components={{
          img: ({ alt }) => <span>[Image omitted: {alt}]</span>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
function Panel({
  id,
  title,
  eyebrow,
  children,
}: {
  id: string;
  title: string;
  eyebrow: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      className="panel backdrop-blur-md bg-slate-900/60 border border-slate-800/80"
    >
      <header className="section-head">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
        </div>
      </header>
      {children}
    </section>
  );
}
function DecisionDetail({ d }: { d: Decision }) {
  return (
    <>
      <div className="row">
        <Badge>{d.id}</Badge>
        <Badge>{d.state}</Badge>
        <Badge>{d.status}</Badge>
        <span>
          Round {d.round_count} · revision {d.revision}
        </span>
      </div>
      <div className="decision-grid">
        <div>
          <h4>Proposals & counter-proposals</h4>
          {d.proposals.map((p, i) => (
            <div className="inset" key={i}>
              <div className="row">
                <Badge agent={p.agent}>{p.agent}</Badge>
                <strong>
                  Proposal {i + 1}
                  {i < 2 ? ` · ${i === 0 ? "A" : "B"}` : ""}
                </strong>
              </div>
              <Prose text={p.content} />
              <details>
                <summary>Rationale / criteria</summary>
                <Json value={p.rationale} />
              </details>
            </div>
          ))}
        </div>
        <div>
          <h4>Peer critique history</h4>
          {!d.critiques.length && <Empty>No peer critique recorded.</Empty>}
          {d.critiques.map((c, i) => (
            <div className="inset" key={i}>
              <div className="row">
                <Badge agent={c.agent}>{c.agent}</Badge>
                <span>Round {c.round}</span>
                <Badge>
                  {c.invalid ? "Invalid critique" : c.payload?.verdict}
                </Badge>
              </div>
              {c.payload && (
                <>
                  <h5>Risks</h5>
                  {c.payload.identified_risks.length ? (
                    c.payload.identified_risks.map((r, j) => (
                      <p key={j} className={r.blocking ? "risk" : ""}>
                        {r.blocking ? "Blocking" : "Nonblocking"}:{" "}
                        {r.description}
                      </p>
                    ))
                  ) : (
                    <p>No risks recorded in this critique.</p>
                  )}
                  <h5>Tradeoff analysis</h5>
                  <dl>
                    {Object.entries(c.payload.tradeoff_analysis).map(
                      ([k, v]) => (
                        <div key={k}>
                          <dt>{k}</dt>
                          <dd>{v}</dd>
                        </div>
                      ),
                    )}
                  </dl>
                  {c.payload.counter_proposal && (
                    <>
                      <h5>Counter-proposal</h5>
                      <Prose text={c.payload.counter_proposal} />
                    </>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      </div>
      {d.state === "ESCALATE" && (
        <p className="notice">
          Escalation reason:{" "}
          {d.invalid_count >= 2
            ? "Repeated invalid critique responses"
            : "Critique round limit reached without qualifying peer agreement"}
          .
        </p>
      )}
      {d.resolution && (
        <div className="resolution">
          <h4>
            {d.status === "human_resolved"
              ? "Human resolution · human_resolved"
              : "Recorded peer resolution"}
          </h4>
          <Prose text={d.resolution} />
          <h5>Final rationale</h5>
          <Json value={d.rationale} />
        </div>
      )}
    </>
  );
}
function HumanForm({
  decision,
  disabled,
  refresh,
}: {
  decision: Decision;
  disabled: boolean;
  refresh: () => Promise<void>;
}) {
  const [directive, setDirective] = useState(""),
    [rationale, setRationale] = useState(""),
    [pending, setPending] = useState(false),
    [notice, setNotice] = useState(""),
    [needsReview, setNeedsReview] = useState(false);
  const last = useRef<{ key: string; id: string } | undefined>(undefined);
  const [reviewedRevision, setReviewedRevision] = useState(decision.revision);
  const changed = reviewedRevision !== decision.revision;
  async function submit(selected?: number) {
    if (pending || disabled || needsReview || changed) return;
    const chosen =
      selected === undefined ? directive : decision.proposals[selected].content;
    if (!chosen.trim() || !rationale.trim()) {
      setNotice("Enter a directive and rationale before submitting.");
      return;
    }
    const payload = {
      expected_revision: decision.revision,
      directive: chosen,
      rationale,
    };
    const key = JSON.stringify(payload);
    if (last.current?.key !== key)
      last.current = { key, id: crypto.randomUUID() };
    setPending(true);
    setNotice("Submitting displayed revision…");
    try {
      const result = await request<Decision>(
        `/api/decisions/${decision.id}/human-resolve`,
        { ...payload, request_id: last.current!.id },
      );
      setNotice(`Recorded ${result.status}. Refreshing authoritative state.`);
    } catch (e) {
      setNotice(
        `${String(e)}. Outcome may be uncertain. State has been refreshed; inspect the current revision and resolution before another explicit submission. Your text is preserved.`,
      );
      setNeedsReview(true);
    } finally {
      await refresh();
      setPending(false);
    }
  }
  return (
    <div className="operator-form">
      <p>
        Submission binds to {decision.id}, revision {decision.revision}.
        Choosing a proposal sends its exact content as the directive.
      </p>
      <label>
        Resolution rationale
        <textarea
          maxLength={100000}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          disabled={pending}
        />
      </label>
      <div className="actions">
        {decision.proposals.map((p, i) => (
          <button
            key={i}
            disabled={
              disabled || pending || needsReview || changed || !rationale.trim()
            }
            onClick={() => void submit(i)}
          >
            Approve {i < 2 ? String.fromCharCode(65 + i) : `proposal ${i + 1}`}{" "}
            · {p.agent} · #{i + 1}
          </button>
        ))}
      </div>
      <label>
        Custom directive
        <textarea
          maxLength={100000}
          value={directive}
          onChange={(e) => setDirective(e.target.value)}
          disabled={pending}
        />
      </label>
      <button
        disabled={
          disabled ||
          pending ||
          needsReview ||
          changed ||
          !directive.trim() ||
          !rationale.trim()
        }
        onClick={() => void submit()}
      >
        Record custom human resolution
      </button>
      <p role="status">
        {changed
          ? "Decision revision changed. Inspect the refreshed proposals and critique before submitting."
          : notice}
      </p>
      {(needsReview || changed) && (
        <button
          disabled={disabled || pending}
          onClick={() => {
            setNeedsReview(false);
            setReviewedRevision(decision.revision);
            setNotice(
              "Current record reviewed. Submit explicitly if still appropriate.",
            );
          }}
        >
          I have reviewed the refreshed decision
        </button>
      )}
    </div>
  );
}
function ActionForm({
  approval,
  disabled,
  refresh,
}: {
  approval: Approval;
  disabled: boolean;
  refresh: () => Promise<void>;
}) {
  const [reason, setReason] = useState(""),
    [pending, setPending] = useState(false),
    [notice, setNotice] = useState(""),
    [review, setReview] = useState(false);
  async function decide(decision: "approved" | "denied") {
    if (
      pending ||
      disabled ||
      review ||
      approval.state !== "pending" ||
      !reason.trim()
    )
      return;
    const id = approval.id,
      fingerprint = approval.fingerprint;
    setPending(true);
    try {
      const result = await request<{ state: string }>(
        `/api/approvals/${id}/decision`,
        { fingerprint, decision, reason },
      );
      setNotice(
        `Recorded ${result.state}. Orchestration may now run an approved exact action.`,
      );
    } catch (e) {
      setNotice(
        `${String(e)}. Outcome may be uncertain. Authoritative state refreshed; inspect the request ID, fingerprint and state before submitting again. Reason preserved.`,
      );
      setReview(true);
    } finally {
      await refresh();
      setPending(false);
    }
  }
  return (
    <>
      <dl>
        <div>
          <dt>Request ID</dt>
          <dd>
            <code>{approval.id}</code>
          </dd>
        </div>
        <div>
          <dt>Fingerprint</dt>
          <dd>
            <code>{approval.fingerprint}</code>
          </dd>
        </div>
      </dl>
      <Json value={approval.document} />
      <p>
        Arguments, repository, worktree, scope, target and context above are the
        persisted, redacted action. Approval authorizes this fingerprint only.
        Redacted details require separate local inspection.
      </p>
      {approval.state === "pending" ? (
        <div className="operator-form">
          <label>
            Reason for approval or denial
            <textarea
              value={reason}
              maxLength={2000}
              onChange={(e) => setReason(e.target.value)}
              disabled={pending}
            />
          </label>
          <div className="actions">
            <button
              disabled={disabled || pending || review || !reason.trim()}
              onClick={() => void decide("approved")}
            >
              Approve exact action
            </button>
            <button
              className="danger"
              disabled={disabled || pending || review || !reason.trim()}
              onClick={() => void decide("denied")}
            >
              Deny exact action
            </button>
          </div>
          {review && (
            <button
              disabled={disabled || pending}
              onClick={() => {
                setReview(false);
                setNotice(
                  "Refreshed request reviewed. Choose an explicit action if still pending.",
                );
              }}
            >
              I have reviewed the refreshed action
            </button>
          )}
        </div>
      ) : (
        <p className="notice">
          {approval.state === "consumed"
            ? "Consumed · this approval cannot be reused."
            : `This request is ${approval.state}; no further decision is offered.`}
        </p>
      )}
      <p role="status">{notice}</p>
    </>
  );
}
function TaskBoard({ data }: { data: Data }) {
  return (
    <div
      className="kanban"
      tabIndex={0}
      role="region"
      aria-label="Task status columns"
    >
      {[
        "planned",
        "assigned",
        "in_progress",
        "review",
        "testing",
        "complete",
        "blocked",
      ].map((status) => (
        <section className="lane" key={status}>
          <h3>
            {status.replace("_", " ")}{" "}
            <span>{data.tasks.filter((t) => t.status === status).length}</span>
          </h3>
          {data.tasks
            .filter((t) => t.status === status)
            .map((t) => (
              <article className="task" key={t.id}>
                <div className="row">
                  <Badge>{t.id}</Badge>
                  <Badge agent={t.assigned_agent}>{t.assigned_agent}</Badge>
                </div>
                <h4>{t.title}</h4>
                <p>Retries: {t.retry_count}</p>
                <details>
                  <summary>Task details</summary>
                  <Prose text={t.description} />
                  <dl>
                    <div>
                      <dt>Dependencies</dt>
                      <dd>{t.dependencies.join(", ") || "None declared"}</dd>
                    </div>
                    <div>
                      <dt>Declared file scope</dt>
                      <dd>{t.touched_files_scope.join(", ")}</dd>
                    </div>
                    <div>
                      <dt>Branch</dt>
                      <dd>{t.branch || "Not created"}</dd>
                    </div>
                    <div>
                      <dt>Worktree</dt>
                      <dd>{t.worktree_path || "Not created"}</dd>
                    </div>
                  </dl>
                  <p>
                    Observable tool calls: {t.tool_calls}/40 · failed attempts:{" "}
                    {t.retry_count}/3
                  </p>
                  <h5>Acceptance criteria</h5>
                  <ul>
                    {t.acceptance_criteria.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                  <h5>Review feedback</h5>
                  {t.review_feedback.length ? (
                    <Json value={t.review_feedback} />
                  ) : (
                    <p>No feedback recorded.</p>
                  )}
                  <details>
                    <summary>Review snapshot / diff</summary>
                    <Json value={t.review} />
                  </details>
                  <h5>Integration attempts</h5>
                  {data.integration_attempts.filter((a) => a.task_id === t.id)
                    .length ? (
                    data.integration_attempts
                      .filter((a) => a.task_id === t.id)
                      .map((a) => (
                        <div key={a.id}>
                          <p>
                            {a.id} · {a.state} ·{" "}
                            {a.document.promoted === true
                              ? "Promoted"
                              : a.document.promoted === false
                                ? "Not promoted"
                                : "Outcome unknown"}
                          </p>
                          <Json
                            value={
                              a.document.test_log ?? "No test log recorded."
                            }
                          />
                        </div>
                      ))
                  ) : (
                    <p>Not run · no integration result recorded.</p>
                  )}
                </details>
              </article>
            ))}
          {!data.tasks.some((t) => t.status === status) && (
            <p className="lane-empty">No tasks</p>
          )}
        </section>
      ))}
    </div>
  );
}
function App() {
  const { data, error, socketState, updated, refresh } = useDashboard();
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 10000);
    return () => clearInterval(timer);
  }, []);
  return (
    <>
      <Network />
      <a className="skip" href="#overview">
        Skip to dashboard
      </a>
      <div className="shell">
        <aside>
          <a className="brand" href="#overview">
            <span className="brand-icon">◈</span> DevMesh
          </a>
          <p className="eyebrow">LOCAL OPERATOR SPACE</p>
          <nav aria-label="Dashboard sections">
            {[
              ["overview", "01", "Overview"],
              ["conversation", "02", "Conversation"],
              ["decisions", "03", "Decision room"],
              ["tasks", "04", "Task board"],
              ["approvals", "05", "Approvals"],
            ].map(([id, n, label]) => (
              <a key={id} href={`#${id}`}>
                <span>{n}</span>
                {label}
              </a>
            ))}
          </nav>
          <div className="sidebar-note">
            <span className="status-dot" /> Milestone 05
            <p>
              Observe. Deliberate.
              <br />
              Keep control.
            </p>
            <small>Local-only, unauthenticated operator interface.</small>
          </div>
        </aside>
        <main>
          <header className="topbar">
            <span>
              WORKSPACE / <strong>DevMesh</strong>
            </span>
            <Badge>LOCAL · 8420</Badge>
          </header>
          <div className="page-title">
            <div>
              <p className="eyebrow">HUMAN + AGENTS</p>
              <h1>
                A shared view.
                <br />
                <span>A deliberate next step.</span>
              </h1>
              <p>
                Conversations, decisions and delivery — grounded in recorded
                state.
              </p>
            </div>
            <div className="connection" role="status">
              <p>
                <i className={`status-dot ${error ? "offline" : ""}`} />
                {error
                  ? "API disconnected"
                  : data
                    ? "API connected"
                    : "Connecting to API"}
              </p>
              <small>Dashboard socket: {socketState}</small>
              <small>
                {updated
                  ? `Snapshot ${updated}`
                  : "Waiting for initial snapshot"}
              </small>
              <button onClick={() => void refresh()}>Refresh state</button>
            </div>
          </div>
          {error && (
            <div className="error" role="alert">
              {error}.{" "}
              {data
                ? "Showing the last successful snapshot; approval controls are disabled."
                : "Start the API on 127.0.0.1:8420 and retry."}
            </div>
          )}
          <Panel
            id="overview"
            eyebrow="01 / PROJECT OVERVIEW"
            title="The work at a glance"
          >
            <div className="overview-grid">
              <ProjectControls
                project={data?.projects[0]}
                disabled={!data || !!error}
                refresh={refresh}
              />
              <div className="agents">
                {["codex", "antigravity"].map((agent) => {
                  const seen = data?.agents.find(
                    (a) => a.name === agent,
                  )?.last_seen;
                  return (
                    <div className={`agent-card ${agent}`} key={agent}>
                      <div className="row">
                        <strong>
                          {agent === "codex" ? "Codex" : "Antigravity"}
                        </strong>
                        <Badge agent={agent}>{heartbeat(seen, clock)}</Badge>
                      </div>
                      <small>
                        {seen
                          ? `Last heartbeat: ${new Date(seen).toLocaleString()}`
                          : "No heartbeat recorded"}
                      </small>
                    </div>
                  );
                })}
                <small>
                  Recent ≤60s · stale 60–180s · offline &gt;180s. A heartbeat
                  does not prove active work.
                </small>
              </div>
            </div>
            <div className="metrics">
              {[
                [
                  "Active tasks",
                  data?.tasks.filter(
                    (t) => !["complete", "blocked"].includes(t.status),
                  ).length,
                ],
                [
                  "Agreed decisions",
                  data?.decisions.filter((d) => d.status === "agreed").length,
                ],
                [
                  "Test gate",
                  data ? testGate(data.integration_attempts) : undefined,
                ],
                [
                  "Pending approvals",
                  data
                    ? data.approvals.filter((a) => a.state === "pending")
                        .length +
                      data.decisions.filter((d) => d.state === "ESCALATE")
                        .length
                    : undefined,
                ],
              ].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>{value ?? "—"}</strong>
                </div>
              ))}
            </div>
            <p className="muted">
              Test gate counts recorded promoted integration attempts; it is not
              a claim that current code passes tests.
            </p>
          </Panel>
          {!data ? (
            <div className="panel" role="status">
              {error
                ? "Dashboard data unavailable. Use Refresh state to retry."
                : "Loading persisted dashboard records…"}
            </div>
          ) : (
            <>
              <Panel
                id="conversation"
                eyebrow="02 / AGENT CONVERSATION"
                title="The conversation, in context"
              >
                <p className="muted">
                  Engine notifications may use “human” as a routing label.
                  Displaying messages never acknowledges them.
                </p>
                {!data.messages.length ? (
                  <Empty>
                    No messages recorded. New messages will appear here.
                  </Empty>
                ) : (
                  <ol className="timeline">
                    {data.messages.map((m) => (
                      <li key={m.id}>
                        <article>
                          <div className="row">
                            <Badge agent={m.sender}>{m.sender}</Badge>
                            <span>→ {m.recipient}</span>
                            <Badge>{m.type}</Badge>
                            <Badge>
                              {m.delivered
                                ? "Acknowledged"
                                : "Pending acknowledgement"}
                            </Badge>
                            <time dateTime={m.created_at}>
                              {new Date(m.created_at).toLocaleString()}
                            </time>
                          </div>
                          <h3>{m.subject || "No subject"}</h3>
                          <details>
                            <summary>Read message / Markdown</summary>
                            <Prose text={m.content} />
                          </details>
                          <details>
                            <summary>Raw content / diff</summary>
                            <pre>{m.content}</pre>
                          </details>
                          <small className="muted">{m.id}</small>
                        </article>
                      </li>
                    ))}
                  </ol>
                )}
              </Panel>
              <Panel
                id="decisions"
                eyebrow="03 / DECISION ROOM"
                title="Reasoning, made visible"
              >
                {!data.decisions.length ? (
                  <Empty>
                    No decisions recorded. Generic reviews do not create
                    agreement.
                  </Empty>
                ) : (
                  data.decisions.map((d) => (
                    <details className="record" key={d.id}>
                      <summary>
                        {d.id} · {d.title} <Badge>{d.status}</Badge>
                      </summary>
                      <DecisionDetail d={d} />
                    </details>
                  ))
                )}
              </Panel>
              <Panel
                id="tasks"
                eyebrow="04 / TASK BOARD"
                title="From plan to verified completion"
              >
                <p className="muted">
                  Read-only task view. Protected transitions remain in the M3
                  review and integration workflow.
                </p>
                <TaskBoard data={data} />
              </Panel>
              <Panel
                id="approvals"
                eyebrow="05 / APPROVAL CENTER"
                title="Your explicit decision"
              >
                <p className="notice">
                  Local operator boundary: identity is unauthenticated. These
                  controls record human resolutions or exact-action decisions;
                  approving an exact action enables orchestration to run it.
                </p>
                <h3>Escalated decisions</h3>
                {!data.decisions.some((d) => d.state === "ESCALATE") && (
                  <Empty>No escalated decisions awaiting resolution.</Empty>
                )}
                {data.decisions
                  .filter((d) => d.state === "ESCALATE")
                  .map((d) => (
                    <article className="record" key={d.id}>
                      <h3>{d.title}</h3>
                      <DecisionDetail d={d} />
                      <HumanForm
                        decision={d}
                        disabled={!!error}
                        refresh={refresh}
                      />
                    </article>
                  ))}
                <h3>Exact-action requests</h3>
                {!data.approvals.length && (
                  <Empty>No exact-action requests recorded.</Empty>
                )}
                {data.approvals.map((a) => (
                  <details className="record" key={a.id}>
                    <summary>
                      {a.document.kind} · {a.id.slice(0, 8)}{" "}
                      <Badge>{a.state}</Badge>
                    </summary>
                    <ActionForm
                      approval={a}
                      disabled={!!error}
                      refresh={refresh}
                    />
                  </details>
                ))}
              </Panel>
            </>
          )}
          <footer>
            SQLite is authoritative. Dashboard notifications have no replay;
            snapshots refresh after reconnect and every 10 seconds.
          </footer>
        </main>
      </div>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
