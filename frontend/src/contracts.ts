export type Proposal = { agent: string; content: string; rationale: unknown };
export type Decision = {
  id: string;
  title: string;
  revision: number;
  state: string;
  status: string;
  round_count: number;
  invalid_count: number;
  proposals: Proposal[];
  critiques: {
    agent: string;
    round: number;
    invalid?: boolean;
    payload?: {
      verdict: string;
      identified_risks: { description: string; blocking: boolean }[];
      counter_proposal: string | null;
      tradeoff_analysis: Record<string, string>;
    };
  }[];
  resolution: string | null;
  rationale: unknown;
  human_resolution: unknown;
  history: unknown[];
};
export type Message = {
  id: string;
  sender: string;
  recipient: string;
  subject: string;
  type: string;
  content: string;
  created_at: string;
  delivered: boolean;
  conversation_id: string;
};
export type Task = {
  id: string;
  title: string;
  description: string;
  assigned_agent: string;
  status: string;
  dependencies: string[];
  touched_files_scope: string[];
  acceptance_criteria: string[];
  branch: string | null;
  worktree_path: string | null;
  review_feedback: unknown[];
  retry_count: number;
  review: unknown;
  history: unknown[];
};
export type Approval = {
  id: string;
  fingerprint: string;
  state: string;
  document: {
    kind: string;
    arguments: string[];
    repository: string;
    worktree: string;
    scope: string[];
    target: string | null;
    context: Record<string, string>;
  };
};
export type Attempt = {
  id: string;
  task_id: string;
  state: string;
  document: { promoted?: boolean; test_log?: string; [key: string]: unknown };
};
export type Snapshot = {
  messages: Message[];
  agents: { name: string; last_seen: string }[];
  integration_attempts: Attempt[];
};
export type Data = Snapshot & {
  decisions: Decision[];
  tasks: Task[];
  approvals: Approval[];
};
export function heartbeat(lastSeen: string | undefined, now = Date.now()) {
  if (!lastSeen || !Number.isFinite(Date.parse(lastSeen))) return "Unknown";
  const age = now - Date.parse(lastSeen);
  return age <= 60_000
    ? "Recent heartbeat"
    : age <= 180_000
      ? "Stale"
      : "Offline";
}
export function uniqueMessages(messages: Message[]) {
  return [...new Map(messages.map((m) => [m.id, m])).values()].sort(
    (a, b) =>
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}
export function testGate(attempts: Attempt[]) {
  if (!attempts.length) return "Not run";
  const unknown = attempts.filter(
    (a) => a.state !== "finished" || typeof a.document.promoted !== "boolean",
  ).length;
  if (unknown) return `${unknown} unknown`;
  const passed = attempts.filter(
    (a) => a.state === "finished" && a.document.promoted === true,
  ).length;
  return `${passed}/${attempts.length} promoted`;
}
