/**
 * CR-SAN-048 §S3 — RED: `WakeSupervisor` state machine (PRD-axi-toon.md §4.7,
 * AC4/AC5/AC6/AC7).
 *
 * The unit under test is the NEW module `src/wake.ts`, exporting `WakeSupervisor`
 * and its `WakeDeps`/`WatcherStatus` shapes (per the CR-215 dispatch prompt — a
 * constructor-injected-deps design, distinct from the old global-setter seams
 * `__setWakeClock`/`__setWakeSleepFn` that `wake.test.ts`/`wake_lifecycle.test.ts`
 * exercise against the OLD `wakeLoop` in `index.ts`; those two files are left
 * untouched — cycle 216 rewrites them against this supervisor).
 *
 * RED reason: `./wake` does not exist yet — the whole file fails module
 * resolution (`Cannot find module './wake'`). This is valid RED (Mode 1:
 * not-yet-existing SUT symbol).
 *
 * Spec ambiguity resolved: the "code:null + signalCode SIGTERM" case (AC6) and
 * the "undecodable stdout → treat as exit 1" case are tested as SEPARATE
 * scenarios (a SIGTERM exec result carries empty stdout, which is itself
 * undecodable) — the SIGTERM test only asserts the notify text contains
 * "SIGTERM" and does not assert the *absence* of "no envelope", since the spec
 * says the composed message carries "the code/signal AND the envelope's error"
 * — both portions may legitimately appear together when stdout is also empty.
 */

import { test, expect, describe, mock } from "bun:test";
import { encode } from "@toon-format/toon";
import { WakeSupervisor, type WakeDeps, type WatcherStatus } from "./wake";

// ─── Envelope fixture builder (mirrors notify.py's `_finish` — PRD §4.5) ────

function notifyEnvelope(opts: {
  exit: number;
  address: string;
  project: string;
  unread?: number[];
  error?: string;
  ok?: boolean;
}): string {
  const ok = opts.ok ?? (opts.exit === 0 || opts.exit === 2 || opts.exit === 5);
  const axi: Record<string, unknown> = {
    verb: "notify",
    ok,
    exit: opts.exit,
    address: opts.address,
    project: opts.project,
    unread: opts.unread ?? [],
  };
  if (opts.error !== undefined) axi.error = opts.error;
  axi.context = { project: opts.project, address: opts.address };
  axi.warnings = [];
  return encode({ axi });
}

// ─── Deferred helper — lets a test resolve an in-flight exec/sleep manually ─

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function makeDeferred<T>(): Deferred<T> {
  let resolveFn!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolveFn = res;
  });
  return { promise, resolve: resolveFn };
}

type ExecResult = { code: number | null; stdout: string; stderr: string; signalCode?: string | null };

interface ExecCall {
  cmd: string;
  args: string[];
  signal: AbortSignal;
  deferred: Deferred<ExecResult>;
}

function makeFakeExec() {
  const calls: ExecCall[] = [];
  const exec = mock((cmd: string, args: string[], opts: { signal: AbortSignal }) => {
    const deferred = makeDeferred<ExecResult>();
    calls.push({ cmd, args, signal: opts.signal, deferred });
    return deferred.promise;
  });
  return { exec, calls };
}

async function flush(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

function makeDeps(overrides?: Partial<WakeDeps>): {
  deps: WakeDeps;
  execCalls: ExecCall[];
  sendUserMessageMock: ReturnType<typeof mock>;
  notifyMock: ReturnType<typeof mock>;
  sleepCalls: number[];
  sleepDeferreds: Deferred<void>[];
  clock: { value: number };
} {
  const { exec, calls: execCalls } = makeFakeExec();
  const sendUserMessageMock = mock((_text: string, _opts: { deliverAs: "followUp" }): void => {});
  const notifyMock = mock((_text: string, _level: "info" | "warning" | "error"): void => {});
  const clock = { value: 0 };
  const sleepCalls: number[] = [];
  const sleepDeferreds: Deferred<void>[] = [];
  const sleep = (ms: number): Promise<void> => {
    sleepCalls.push(ms);
    const d = makeDeferred<void>();
    sleepDeferreds.push(d);
    return d.promise;
  };
  const resolveFn = (args: string[]): [string, string[]] => ["sandesh", args];
  const deps: WakeDeps = {
    exec,
    sendUserMessage: sendUserMessageMock,
    notify: notifyMock,
    now: () => clock.value,
    sleep,
    resolve: resolveFn,
    ...overrides,
  };
  return { deps, execCalls, sendUserMessageMock, notifyMock, sleepCalls, sleepDeferreds, clock };
}

// ─── AC4 — exit 0 (mail): id-set dedup, message + relaunch timing ──────────

describe("AC4 — exit 0 (mail) dedup + relaunch timing", () => {
  test("first exit 0 with ids [12,13] sends a followUp message naming them and relaunches with no sleep", async () => {
    const { deps, execCalls, sendUserMessageMock, sleepCalls } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");
    expect(execCalls.length).toBe(1);

    execCalls[0].deferred.resolve({
      code: 0,
      stdout: notifyEnvelope({ exit: 0, address: "Mainline - Demo", project: "Demo", unread: [12, 13] }),
      stderr: "",
      signalCode: null,
    });
    await flush();

    expect(sendUserMessageMock.mock.calls.length).toBe(1);
    const [text, opts] = sendUserMessageMock.mock.calls[0] as [string, { deliverAs: string }];
    expect(text).toContain("12, 13");
    expect(text).toContain("Mainline - Demo");
    expect(opts).toEqual({ deliverAs: "followUp" });

    expect(sleepCalls.length).toBe(0); // relaunch is immediate — no sleep call
    expect(execCalls.length).toBe(2);

    const status = sup.status();
    expect(status[0].lastIds).toEqual([12, 13]);
  });

  test("second exit 0 with the same id SET (different order [13,12]) sends no message and delays relaunch 30s", async () => {
    const { deps, execCalls, sendUserMessageMock, sleepCalls, sleepDeferreds } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({
      code: 0,
      stdout: notifyEnvelope({ exit: 0, address: "Mainline - Demo", project: "Demo", unread: [12, 13] }),
      stderr: "",
      signalCode: null,
    });
    await flush();
    expect(execCalls.length).toBe(2); // relaunched from the first exit

    execCalls[1].deferred.resolve({
      code: 0,
      stdout: notifyEnvelope({ exit: 0, address: "Mainline - Demo", project: "Demo", unread: [13, 12] }),
      stderr: "",
      signalCode: null,
    });
    await flush();

    expect(sendUserMessageMock.mock.calls.length).toBe(1); // unchanged — no new message
    expect(sleepCalls).toEqual([30000]);
    expect(execCalls.length).toBe(2); // NOT relaunched yet — waiting on the sleep

    sleepDeferreds[0].resolve();
    await flush();
    expect(execCalls.length).toBe(3); // relaunched only once the sleep resolves
  });

  test("third exit 0 with a different id set [14] sends a message naming 14, relaunches immediately, and updates lastIds", async () => {
    const { deps, execCalls, sendUserMessageMock, sleepCalls, sleepDeferreds } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({
      code: 0,
      stdout: notifyEnvelope({ exit: 0, address: "Mainline - Demo", project: "Demo", unread: [12, 13] }),
      stderr: "",
      signalCode: null,
    });
    await flush();
    execCalls[1].deferred.resolve({
      code: 0,
      stdout: notifyEnvelope({ exit: 0, address: "Mainline - Demo", project: "Demo", unread: [13, 12] }),
      stderr: "",
      signalCode: null,
    });
    await flush();
    sleepDeferreds[0].resolve();
    await flush();
    expect(execCalls.length).toBe(3);

    execCalls[2].deferred.resolve({
      code: 0,
      stdout: notifyEnvelope({ exit: 0, address: "Mainline - Demo", project: "Demo", unread: [14] }),
      stderr: "",
      signalCode: null,
    });
    await flush();

    expect(sendUserMessageMock.mock.calls.length).toBe(2);
    const [text] = sendUserMessageMock.mock.calls[1] as [string, unknown];
    expect(text).toContain("14");
    expect(sleepCalls).toEqual([30000]); // no additional sleep was requested
    expect(execCalls.length).toBe(4); // immediate relaunch

    const status = sup.status();
    expect(status[0].lastIds).toEqual([14]);
  });
});

// ─── AC5 — exit 2 (timeout): silent relaunch, 3rd-in-60s warning burst ─────

describe("AC5 — exit 2 (timeout) burst warning", () => {
  test("three exit-2s in 60s → one warning; a 4th in-window → none; a new window → a second warning", async () => {
    const { deps, execCalls, notifyMock, sleepCalls, clock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    const resolveExit2 = async (idx: number, atTime: number): Promise<void> => {
      clock.value = atTime;
      execCalls[idx].deferred.resolve({
        code: 2,
        stdout: notifyEnvelope({ exit: 2, address: "Mainline - Demo", project: "Demo" }),
        stderr: "",
        signalCode: null,
      });
      await flush();
    };

    await resolveExit2(0, 0);
    expect(notifyMock.mock.calls.length).toBe(0);
    expect(execCalls.length).toBe(2);

    await resolveExit2(1, 20_000);
    expect(notifyMock.mock.calls.length).toBe(0);
    expect(execCalls.length).toBe(3);

    await resolveExit2(2, 40_000);
    expect(notifyMock.mock.calls.length).toBe(1);
    const [warnText, warnLevel] = notifyMock.mock.calls[0] as [string, string];
    expect(warnLevel).toBe("warning");
    expect(warnText).toContain("Mainline - Demo");
    expect(warnText).toContain("3 timeouts in 60s");
    expect(execCalls.length).toBe(4);

    await resolveExit2(3, 50_000); // 4th within the same window — no re-notify
    expect(notifyMock.mock.calls.length).toBe(1);
    expect(execCalls.length).toBe(5);

    await resolveExit2(4, 130_000); // new window (1st)
    expect(notifyMock.mock.calls.length).toBe(1);
    expect(execCalls.length).toBe(6);

    await resolveExit2(5, 140_000); // new window (2nd)
    expect(notifyMock.mock.calls.length).toBe(1);
    expect(execCalls.length).toBe(7);

    await resolveExit2(6, 150_000); // new window (3rd) — second warning
    expect(notifyMock.mock.calls.length).toBe(2);
    expect(execCalls.length).toBe(8);

    expect(sleepCalls.length).toBe(0); // exit-2 relaunches are never delayed by sleep
  });
});

// ─── AC6 — terminal exits stop the loop ────────────────────────────────────

describe("AC6 — terminal exits stop the loop", () => {
  test("exit 5 (dedup) stops silently — no message, no notify, no relaunch", async () => {
    const { deps, execCalls, sendUserMessageMock, notifyMock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({
      code: 5,
      stdout: notifyEnvelope({ exit: 5, address: "Mainline - Demo", project: "Demo" }),
      stderr: "",
      signalCode: null,
    });
    await flush();

    expect(sendUserMessageMock.mock.calls.length).toBe(0);
    expect(notifyMock.mock.calls.length).toBe(0);
    expect(execCalls.length).toBe(1); // no relaunch

    const status = sup.status();
    expect(status[0].running).toBe(false);
    expect(status[0].lastExit).toBe(5);
  });

  test("exit 1 with error 'not registered' stops and surfaces one error notify naming the code and reason", async () => {
    const { deps, execCalls, notifyMock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({
      code: 1,
      stdout: notifyEnvelope({ exit: 1, ok: false, address: "Mainline - Demo", project: "Demo", error: "not registered" }),
      stderr: "",
      signalCode: null,
    });
    await flush();

    expect(notifyMock.mock.calls.length).toBe(1);
    const [text, level] = notifyMock.mock.calls[0] as [string, string];
    expect(level).toBe("error");
    expect(text).toContain("1");
    expect(text).toContain("not registered");
    expect(execCalls.length).toBe(1); // no relaunch
    expect(sup.status()[0].running).toBe(false);
  });

  test("exit 3 with error 'tombstoned' stops and surfaces one error notify naming the code and reason", async () => {
    const { deps, execCalls, notifyMock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({
      code: 3,
      stdout: notifyEnvelope({ exit: 3, ok: false, address: "Mainline - Demo", project: "Demo", error: "tombstoned — shutting down (evicted)." }),
      stderr: "",
      signalCode: null,
    });
    await flush();

    expect(notifyMock.mock.calls.length).toBe(1);
    const [text, level] = notifyMock.mock.calls[0] as [string, string];
    expect(level).toBe("error");
    expect(text).toContain("3");
    expect(text).toContain("tombstoned");
    expect(execCalls.length).toBe(1);
    expect(sup.status()[0].running).toBe(false);
  });

  test("exit 4 with error 'evicted' stops and surfaces one error notify naming the code and reason", async () => {
    const { deps, execCalls, notifyMock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({
      code: 4,
      stdout: notifyEnvelope({ exit: 4, ok: false, address: "Mainline - Demo", project: "Demo", error: "evicted — another notifier took over 'Mainline - Demo'." }),
      stderr: "",
      signalCode: null,
    });
    await flush();

    expect(notifyMock.mock.calls.length).toBe(1);
    const [text, level] = notifyMock.mock.calls[0] as [string, string];
    expect(level).toBe("error");
    expect(text).toContain("4");
    expect(text).toContain("evicted");
    expect(execCalls.length).toBe(1);
    expect(sup.status()[0].running).toBe(false);
  });

  test("code:null with signalCode SIGTERM stops and surfaces one error notify naming the signal", async () => {
    const { deps, execCalls, notifyMock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({ code: null, stdout: "", stderr: "killed", signalCode: "SIGTERM" });
    await flush();

    expect(notifyMock.mock.calls.length).toBe(1);
    const [text, level] = notifyMock.mock.calls[0] as [string, string];
    expect(level).toBe("error");
    expect(text).toContain("SIGTERM");
    expect(execCalls.length).toBe(1); // no relaunch
    expect(sup.status()[0].running).toBe(false);
  });

  test("undecodable stdout is treated as exit 1 with error 'no envelope'", async () => {
    const { deps, execCalls, notifyMock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    execCalls[0].deferred.resolve({ code: 0, stdout: "not a toon envelope at all", stderr: "", signalCode: null });
    await flush();

    expect(notifyMock.mock.calls.length).toBe(1);
    const [text, level] = notifyMock.mock.calls[0] as [string, string];
    expect(level).toBe("error");
    expect(text).toContain("no envelope");
    expect(execCalls.length).toBe(1); // no relaunch
    expect(sup.status()[0].running).toBe(false);
  });
});

// ─── AC7 — one loop per address, concurrency, stop/status ──────────────────

describe("AC7 — one loop per address, concurrent addresses, stop/status", () => {
  test("start() twice for the same address: second call reports already:true and spawns nothing", () => {
    const { deps, execCalls } = makeDeps();
    const sup = new WakeSupervisor(deps);

    const first = sup.start("Mainline - Demo", "Demo");
    expect(first.already).toBe(false);
    expect(execCalls.length).toBe(1);

    const second = sup.start("Mainline - Demo", "Demo");
    expect(second.already).toBe(true);
    expect(execCalls.length).toBe(1); // nothing spawned by the second call
  });

  test("two different addresses run concurrently; status() reports both as running", () => {
    const { deps, execCalls } = makeDeps();
    const sup = new WakeSupervisor(deps);

    sup.start("Mainline - Demo", "Demo");
    sup.start("Track 1 - Demo", "Demo");
    expect(execCalls.length).toBe(2);

    const status = sup.status();
    expect(status.length).toBe(2);
    expect(status.every((s: WatcherStatus) => s.running === true)).toBe(true);
    const addresses = status.map((s: WatcherStatus) => s.address).sort();
    expect(addresses).toEqual(["Mainline - Demo", "Track 1 - Demo"]);
  });

  test("stop() with no address stops every running loop, aborts each child's signal, and returns the count", () => {
    const { deps, execCalls } = makeDeps();
    const sup = new WakeSupervisor(deps);

    sup.start("Mainline - Demo", "Demo");
    sup.start("Track 1 - Demo", "Demo");

    const result = sup.stop();
    expect(result).toEqual({ stopped: 2 });
    expect(execCalls[0].signal.aborted).toBe(true);
    expect(execCalls[1].signal.aborted).toBe(true);

    const status = sup.status();
    expect(status.every((s: WatcherStatus) => s.running === false)).toBe(true);
  });

  test("stop('nobody') stops nothing and returns stopped: 0", () => {
    const { deps } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    const result = sup.stop("nobody");
    expect(result).toEqual({ stopped: 0 });
  });

  test("a stopped loop's pending exit resolves after the abort but must NOT relaunch or message", async () => {
    const { deps, execCalls, sendUserMessageMock, notifyMock } = makeDeps();
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");
    sup.stop("Mainline - Demo");

    // Simulate the child actually terminating (in response to the abort) AFTER stop() ran.
    execCalls[0].deferred.resolve({ code: null, stdout: "", stderr: "", signalCode: "SIGTERM" });
    await flush();

    expect(sendUserMessageMock.mock.calls.length).toBe(0);
    expect(notifyMock.mock.calls.length).toBe(0);
    expect(execCalls.length).toBe(1); // no relaunch spawned
  });
});

// ─── Argv contract — resolve()'s output is passed to exec verbatim ─────────

describe("start() argv — resolve()'s output is passed to exec verbatim", () => {
  test("exec receives resolve()'s [cmd, args] for --project/--format toon/notify/--to", () => {
    const { deps, execCalls } = makeDeps({
      resolve: (args: string[]): [string, string[]] => ["/opt/sandesh/bin/sandesh", args],
    });
    const sup = new WakeSupervisor(deps);
    sup.start("Mainline - Demo", "Demo");

    expect(execCalls.length).toBe(1);
    expect(execCalls[0].cmd).toBe("/opt/sandesh/bin/sandesh");
    expect(execCalls[0].args).toEqual([
      "--project",
      "Demo",
      "--format",
      "toon",
      "notify",
      "--to",
      "Mainline - Demo",
    ]);
  });
});
