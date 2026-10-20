import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Data,
  Snapshot,
  Decision,
  Task,
  Approval,
  Project,
} from "./contracts";
import { uniqueMessages } from "./contracts";
export const API = "http://127.0.0.1:8420";
export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(API + path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      `HTTP ${response.status}: ${error.detail || response.statusText}`,
    );
  }
  return response.json();
}
export function useDashboard() {
  const [data, setData] = useState<Data>();
  const [error, setError] = useState("");
  const [socketState, setSocketState] = useState("Connecting");
  const [updated, setUpdated] = useState("");
  const alive = useRef(false),
    inFlight = useRef<Promise<void> | null>(null),
    queued = useRef(false);
  const refresh = useCallback((): Promise<void> => {
    if (inFlight.current) {
      queued.current = true;
      return inFlight.current;
    }
    const run = async () => {
      do {
        queued.current = false;
        try {
          const [snapshot, decisions, tasks, approvals, projects] =
            await Promise.all([
              request<Snapshot>("/api/dashboard/snapshot"),
              request<{ decisions: Decision[] }>(
                "/api/decisions?open_only=false",
              ),
              request<{ tasks: Task[] }>("/api/tasks"),
              request<{ requests: Approval[] }>("/api/approvals"),
              request<{ projects: Project[] }>("/api/projects"),
            ]);
          if (alive.current) {
            setData({
              ...snapshot,
              messages: uniqueMessages(snapshot.messages),
              decisions: decisions.decisions,
              tasks: tasks.tasks,
              approvals: approvals.requests,
              projects: projects.projects,
            });
            setError("");
            setUpdated(new Date().toLocaleTimeString());
          }
        } catch (e) {
          if (alive.current)
            setError(e instanceof Error ? e.message : "API unavailable");
        }
      } while (queued.current && alive.current);
    };
    inFlight.current = run().finally(() => {
      inFlight.current = null;
    });
    return inFlight.current;
  }, []);
  useEffect(() => {
    alive.current = true;
    let socket: WebSocket,
      reconnect: ReturnType<typeof setTimeout>,
      debounce: ReturnType<typeof setTimeout>,
      stable: ReturnType<typeof setTimeout>,
      retries = 0;
    const connect = () => {
      if (!alive.current) return;
      setSocketState(retries ? "Reconnecting" : "Connecting");
      socket = new WebSocket("ws://127.0.0.1:8420/ws/dashboard");
      socket.onopen = () => {
        setSocketState("Connected");
        void refresh();
        stable = setTimeout(() => {
          retries = 0;
        }, 10000);
      };
      socket.onmessage = () => {
        clearTimeout(debounce);
        debounce = setTimeout(() => void refresh(), 200);
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        clearTimeout(stable);
        if (alive.current) {
          setSocketState("Reconnecting");
          reconnect = setTimeout(
            connect,
            Math.min(30000, 1000 * 2 ** Math.min(retries++, 5)),
          );
        }
      };
    };
    void refresh();
    connect();
    const poll = setInterval(() => void refresh(), 10000);
    return () => {
      alive.current = false;
      clearInterval(poll);
      clearTimeout(reconnect);
      clearTimeout(debounce);
      clearTimeout(stable);
      socket.onclose = null;
      socket.close();
    };
  }, [refresh]);
  return { data, error, socketState, updated, refresh };
}
