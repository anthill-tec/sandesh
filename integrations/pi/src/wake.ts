/**
 * Wake supervisor (CR-SAN-048 §S3 — PRD-axi-toon.md §4.7).
 *
 * One `sandesh --project <P> --format toon notify --to <A>` child per address,
 * relaunched according to the notify exit table:
 *   - 0       → mail. Same id-set as last time ⇒ no message, relaunch after 30 s;
 *               different ⇒ `sendUserMessage(…, {deliverAs:"followUp"})`, relaunch now.
 *   - 2       → timeout. Relaunch silently; the 3rd within 60 s ⇒ one warning.
 *   - 5       → dedup (another watcher owns the address). Stop silently.
 *   - 1/3/4   → error / tombstoned / evicted. Stop + one error notify.
 *   - signal  → (code null) stop + one error notify naming the signal.
 * Undecodable stdout is treated as exit 1 with error "no envelope".
 *
 * All host effects (exec, messaging, notify, clock, sleep, CLI resolution) are
 * constructor-injected via `WakeDeps` so the state machine is unit-testable.
 */

import { decodeEnvelope } from "./toon";

export interface WakeExecResult {
  code: number | null;
  stdout: string;
  stderr: string;
  signalCode?: string | null;
}

export interface WakeDeps {
  exec: (cmd: string, args: string[], opts: { signal: AbortSignal }) => Promise<WakeExecResult>;
  sendUserMessage: (text: string, opts: { deliverAs: "followUp" }) => void;
  notify: (text: string, level: "info" | "warning" | "error") => void;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  resolve: (args: string[]) => [string, string[]];
}

export interface WatcherStatus {
  address: string;
  project: string;
  running: boolean;
  pid?: number;
  startedAt: number;
  lastExit: number | null;
  lastIds: number[];
  timeoutExits: number[];
}

export interface StartResult {
  already: boolean;
  status: WatcherStatus;
}

interface Entry extends WatcherStatus {
  stopped: boolean;
  generation: number;
  controller: AbortController;
}

const SAME_IDS_DELAY_MS = 30_000;
const TIMEOUT_WINDOW_MS = 60_000;
const TIMEOUT_BURST = 3;

function snapshot(e: Entry): WatcherStatus {
  return {
    address: e.address,
    project: e.project,
    running: e.running,
    pid: e.pid,
    startedAt: e.startedAt,
    lastExit: e.lastExit,
    lastIds: [...e.lastIds],
    timeoutExits: [...e.timeoutExits],
  };
}

function sameIdSet(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  const sa = [...a].sort((x, y) => x - y);
  const sb = [...b].sort((x, y) => x - y);
  return sa.every((v, i) => v === sb[i]);
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((v) => typeof v === "number");
}

export class WakeSupervisor {
  private readonly entries = new Map<string, Entry>();

  constructor(private readonly deps: WakeDeps) {}

  start(address: string, project: string): StartResult {
    const existing = this.entries.get(address);
    if (existing !== undefined && existing.running) {
      return { already: true, status: snapshot(existing) };
    }
    const entry: Entry = {
      address,
      project,
      running: false,
      startedAt: this.deps.now(),
      lastExit: null,
      lastIds: [],
      timeoutExits: [],
      stopped: false,
      generation: 0,
      controller: new AbortController(),
    };
    this.entries.set(address, entry);
    this.launch(entry);
    return { already: false, status: snapshot(entry) };
  }

  stop(address?: string): { stopped: number } {
    let stopped = 0;
    for (const entry of this.entries.values()) {
      if (address !== undefined && entry.address !== address) continue;
      if (!entry.running) continue;
      entry.stopped = true;
      entry.running = false;
      entry.controller.abort();
      stopped += 1;
    }
    return { stopped };
  }

  status(): WatcherStatus[] {
    return [...this.entries.values()].map(snapshot);
  }

  private launch(entry: Entry): void {
    entry.generation += 1;
    const gen = entry.generation;
    entry.controller = new AbortController();
    entry.running = true;
    entry.pid = undefined;
    const [cmd, args] = this.deps.resolve([
      "--project",
      entry.project,
      "--format",
      "toon",
      "notify",
      "--to",
      entry.address,
    ]);
    // Fire-and-forget: the child's exit drives the next transition.
    this.deps.exec(cmd, args, { signal: entry.controller.signal }).then(
      (r) => this.onExit(entry, gen, r),
      (err: unknown) => this.onExit(entry, gen, { code: 1, stdout: "", stderr: String(err) }),
    );
  }

  private async onExit(entry: Entry, gen: number, r: WakeExecResult): Promise<void> {
    if (entry.stopped || gen !== entry.generation) return;

    let exit: number | null = r.code;
    let ids: number[] = [];
    let err: string | undefined;
    try {
      const env = decodeEnvelope(r.stdout);
      if (typeof env.fields.exit === "number") exit = env.fields.exit;
      if (isNumberArray(env.fields.unread)) ids = env.fields.unread;
      err = env.error;
    } catch {
      exit = 1;
      err = "no envelope";
    }
    const signal = r.code === null && r.signalCode ? r.signalCode : undefined;
    entry.lastExit = exit;

    if (signal === undefined && exit === 0) {
      if (sameIdSet(ids, entry.lastIds)) {
        await this.deps.sleep(SAME_IDS_DELAY_MS);
        if (entry.stopped || gen !== entry.generation) return;
      } else {
        this.deps.sendUserMessage(
          `Unread Sandesh mail for ${entry.address}: ${ids.join(", ")}. Call sandesh_fetch for it.`,
          { deliverAs: "followUp" },
        );
        entry.lastIds = ids;
      }
      this.launch(entry);
      return;
    }

    if (signal === undefined && exit === 2) {
      const now = this.deps.now();
      entry.timeoutExits.push(now);
      entry.timeoutExits = entry.timeoutExits.filter((t) => t >= now - TIMEOUT_WINDOW_MS);
      if (entry.timeoutExits.length === TIMEOUT_BURST) {
        this.deps.notify(
          `Sandesh watcher for ${entry.address}: ${TIMEOUT_BURST} timeouts in 60s — check the CLI/store`,
          "warning",
        );
      }
      this.launch(entry);
      return;
    }

    entry.running = false;
    if (signal === undefined && exit === 5) return; // dedup — another watcher owns it
    const label = signal !== undefined ? `signal ${signal}` : `exit ${exit}`;
    this.deps.notify(
      `Sandesh watcher for ${entry.address} stopped (${label}): ${err ?? ""}`.trim(),
      "error",
    );
  }
}
