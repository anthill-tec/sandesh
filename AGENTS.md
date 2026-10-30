# AGENTS.md — Sandesh

Project conventions for agents working in this repo. The architecture, the locked
messaging semantics and the gotchas live in **`CLAUDE.md`**. Read it first. This file
holds what `CLAUDE.md` does not: the conventions of the Pi extension, the project's
bun stack.

---

## The Pi extension (`integrations/pi/`) — bun stack

### What it is

`@anthill-tec/sandesh-pi` is a TypeScript Pi extension. It exposes Sandesh as 16 native
Pi tools plus the `/sandesh-watcher` command. It publishes four source files:

| file | role |
|---|---|
| `src/index.ts` | tool registration (TypeBox schemas) + argv building + result mapping; the session_start probe / version gate |
| `src/wake.ts` | `WakeSupervisor`: the state machine that runs one `notify` child per address, every host effect injected through `WakeDeps` |
| `src/toon.ts` | decodes the CLI's AXI/TOON envelopes |
| `src/identity.ts` | the nudge when `.env` names a `SANDESH_*` identity the process does not export |

**Boundaries.**
- The extension is a thin translator: tool params ↔ `sandesh` CLI argv, and CLI output ↔ tool
  result.
- It never imports Sandesh-core, and holds no messaging logic. Behaviour belongs in the Python
  CLI, not the shim.
- A Pi-only change leaves `git diff <base>..HEAD -- sandesh/` empty.
- Every tool a spec lists is really registered with `pi.registerTool`; count them against the
  acceptance criteria.

**Sources of the API.**
- `@earendil-works/pi-coding-agent`: `ExtensionAPI`, `ExtensionContext`, `AgentToolResult`,
  `ToolDefinition`, `ExecResult`; `pi.registerTool`, `pi.registerCommand`, `pi.exec`, `pi.on`,
  `pi.sendUserMessage`.
- `@earendil-works/pi-ai`: `StringEnum`, for every `kind`-style enum parameter.
- `typebox`: `Type.Object` / `String` / `Array` / `Boolean` / `Optional` for every other
  parameter.
- These three are peer dependencies. The one runtime dependency is `@toon-format/toon`.

### Running it

- The bun package dir is **`integrations/pi/`**. Every bun client run names it:
  `bun-crucible.py <verb> --package-dir integrations/pi`. Do not rely on a client default.
  - `test [--tests src/<file>.test.ts]` runs targeted tests.
  - `regression` runs the full suite.
  - `check` runs `tsc --noEmit`.
- `tsconfig.json` is `strict`, with `isolatedModules` and `verbatimModuleSyntax`: a type-only
  import is written `import type`.
- There is no `bunfig.toml`.
- No `.only` is ever committed. The only skips are the real-binary guards described under
  "Real-binary tests".

### The argv contract

Every verb tool builds its argv the same way:

```
resolveSandesh(["--format", "toon", ...projectPrefix(project_id), <verb>, ...flags])
  → pi.exec(cmd, args, { signal, timeout: cliTimeoutMs })
```

**The binary.** It is resolved once, by the session_start `--version` probe:
1. `$SANDESH_BIN`, used exactly as given, with no fallback;
2. else `sandesh` on `PATH`;
3. else `uvx --from sandesh-relay[migrate] sandesh`.

`MIN_CLI_VERSION` in `index.ts` gates the session on the CLI version.

**`--project P`.** An explicit `project_id` wins. Otherwise `$SANDESH_PROJECT`, read at call
time. Otherwise the flag is omitted.

**The mapping.** A spec's CLI-mapping table is the exact argv contract. The standing
mappings:
- `to` and `cc` arrays are comma-joined;
- `parent_id` → `reply --to-msg`;
- `msg_id` → `thread --id`;
- inverted flags: `unread_only: false` → `--all`, and `mark: false` → `--peek`;
- `fields` → `--fields a,b,c`, and `full: true` → `--full`;
- the inbox/fetch filters: `sender` → `--from`, `sender_project` → `--from-project`, `kind`,
  `since`, `until`, and `subject_like` → `--subject`.

A flag is emitted only when its parameter is given. Nothing is emitted for a default.

### Result mapping — errors are results

| CLI outcome | tool result |
|---|---|
| exit 0 | stdout (the TOON envelope), passed through untouched as `{content:[{type:"text",text}]}` |
| non-zero, and stdout decodes as an envelope | **returned** as the result. This covers `ok:false` + `error` + `help[]`, and `unregister`'s `tombstoned` (exit 3). The agent reads it; it is not a failure |
| non-zero, and no envelope (CLI missing, crash) | `execute` **throws** `Error("sandesh <verb> failed (exit N): <stderr>")`, and Pi sets `isError` |
| `killed: true` | throws `sandesh <verb> aborted` when the caller's `signal` is aborted, otherwise `sandesh <verb> timed out after <n>s` |

**Timeouts.** Pi's `exec` reports both a timeout and an abort as `{code: 0, killed: true}`, so
`killed` is checked before `code`. Every CLI exec carries `timeout: CLI_TIMEOUT_MS` (240 s);
that includes the `--version` probes and `init --check`. The long-lived `notify` watcher child
never carries a timeout.

`execute` is async. Every `pi.exec` is awaited, and the tool's `signal` is passed through.

### Tests

**Unit tests mock `pi`. They never run the real CLI.**
- Build a fake `ExtensionAPI` per test:
  - `registerTool` is a spy that captures each tool definition (name, `label`, `description`,
    `parameters`, `execute`) into a map by name;
  - `registerCommand` and `on` are mocks;
  - `exec` is `mock(async () => ExecResult)`, scripted per test.
- Call `registerExtension(fakePi)`, look the tool up, then call
  `tool.execute(id, params, signal, onUpdate, ctx)`.
- Pin argv **positively and exactly**:
  `expect(args).toEqual(["--format","toon","--project","X","send","--from",…])`. Add a
  **negative** pin for every flag that must be omitted.
- Pin the result mapping for each row of the result table above.
- The wake supervisor is unit-tested through `WakeSupervisor` with fake `WakeDeps` (`exec`,
  `sendUserMessage`, `notify`, `now`, `sleep`, `resolve`): no real child, no real clock.
- **Test seams.** Production code never calls them:
  - `resetExtensionState()` in `afterEach` stops every watcher and resets the binary
    resolution;
  - `__setCliTimeoutMs()` and `__setStartSettleMs()` shorten the timeouts.

**Real-binary tests are deliberate and few.**
- `smoke.test.ts` is the one suite that spawns the real `sandesh`. It proves the shim's own
  argv against the real CLI: the setup → register → send → fetch round trip, the
  `$SANDESH_BIN` override, and the supervised wake end to end.
- It resolves the binary as `$SANDESH_BIN` → `<repo>/.venv/bin/sandesh` → `PATH`, and is
  `skipIf`-guarded when no binary answers.
- Every spawn passes a temp `XDG_DATA_HOME` in the subprocess env. It never touches the real
  global store (see `CLAUDE.md`, "The global store is shared…").
- A new seam that crosses the CLI boundary gets a smoke-level proof, not only a mocked one.
- `dependency`, `package` and `wake_lifecycle` tests spawn `npm pack --dry-run`, and
  `version_gate` spawns `git`; none of them spawns `sandesh`.

**Standing guards:**
- every tool `description` is at most 320 characters (`axi_passthrough.test.ts`); put detail
  in the parameter descriptions instead;
- doc-pin tests that assert agent-facing docs contain required text are an accepted
  convention;
- `package.json`'s `version` is written only by `scripts/release.sh set-version`, which also
  writes `server.json`, and `version_sync.test.ts` guards it.

### Review and FIX heuristics

Look for:
- wrong, reordered or missing argv, or a flag emitted at its default;
- a floating promise or a missing `await`;
- logic leaking into the shim that belongs in the CLI;
- `code` checked before `killed`;
- a mock that hides a real exec contract (`killed: true`, a `null` code on a signal);
- a test that spawns the real CLI without a temp `XDG_DATA_HOME`;
- a wake test that sleeps on the real clock.
