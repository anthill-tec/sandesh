# CR-SAN-051 — sandesh-pi: session_start nudge when the `SANDESH_*` identity is in `./.env` but not exported

**Type:** feature
**Priority:** High (a silent "no ambient view, wake not armed" on every session launched from a shell that did not load `.env`)
**Depends on:** CR-SAN-048
**Labels:** pi, bun, session_start, docs
**Wave:** 16 · **Release:** 0.4.0
**Design reference:** [PRD-axi-toon](../research/PRD-axi-toon.md) §4.6 (P7 ambient context + the v1.2
unexported-identity nudge), §4.7 (D4 arming — the nudge does not arm)

## Context

The extension takes its identity from `$SANDESH_ADDRESS` + `$SANDESH_PROJECT` only. A project's Model B
registry `.env` carries both, but nothing exports it unless the launching shell does; on a shell where
that fails (fish rejects the POSIX `set -a; . ./.env` idiom) `session_start` sees no identity, injects
no ambient view, skips the tool-started notice, and says nothing about why. Loading `.env` is the
shell's job (direnv — PRD §4.6 v1.2); the extension only reports the cause.

## Scope

### §S1 — the nudge (bun)

- New module `integrations/pi/src/identity.ts` exporting:
  - `IDENTITY_KEYS = ["SANDESH_ADDRESS", "SANDESH_PROJECT"] as const`.
  - `unexportedIdentityKeys(envText: string, env: Record<string, string | undefined>): string[]` — pure.
    Returns, in `IDENTITY_KEYS` order, each key that `envText` **assigns** and `env` has unset or
    empty (`""`). A line assigns key `K` iff it matches `^\s*(?:export\s+)?K\s*=` (multiline); a
    commented line (`# K=…`) does not. The value is never read, parsed, or returned.
  - `unexportedIdentityNotice(keys: string[]): string` — exactly
    `` `Sandesh identity found in ./.env but not exported: ${keys.join(", ")}. Load it at the shell (direnv: an .envrc containing \`dotenv\`, then \`direnv allow\`) and restart pi — until then there is no ambient status and no wake.` ``
- `index.ts` `session_start`: FIRST thing after `latestUi = ctx.ui` (before the CLI probe — the
  nudge is an environment fact, independent of the CLI), read `join(ctx.cwd, ".env")` as UTF-8; if
  `unexportedIdentityKeys(text, process.env)` is non-empty, `ctx.ui.notify(unexportedIdentityNotice(keys), "warning")`
  once. Any failure (no `.env`, unreadable, `ctx.cwd` missing or not a string) → no nudge, no throw.
  Only `<ctx.cwd>/.env` is read — no parent walk, no other file.
- Read-only: `process.env` is never written. The rest of `session_start` (probe, provision nudge,
  ambient view, arming) is unchanged and still keys off `process.env` alone.

**Surfaces (verified 2026-09-29):** `integrations/pi/src/index.ts` `session_start` handler (l.1114–1182);
`ExtensionContext.cwd: string` (`@earendil-works/pi-coding-agent` `extensions/types.d.ts`).

### §S2 — loading guidance (docs)

- `docs/USER_GUIDE.md` §"For Pi extension users": a short "Your identity" paragraph — the extension
  reads `$SANDESH_ADDRESS`/`$SANDESH_PROJECT` from the environment only; load the project's `.env` at
  the shell with direnv (`.envrc` = `dotenv`; hook line for fish `direnv hook fish | source`, bash
  `eval "$(direnv hook bash)"`; `direnv allow` once); and that the extension warns at session start
  when it finds the identity in `./.env` unexported. The same paragraph corrects the stale
  "≤ 6-line" ambient block to "≤ 12-line" (PRD §4.0 P7, amended at CR-SAN-048 VERIFY).
- `integrations/pi/README.md`, next to its environment-variable table: one sentence + the direnv
  pointer (the extension never reads `.env`; load it at the shell; the session-start warning).

## Acceptance criteria

- [ ] **AC1** — `unexportedIdentityKeys` (unit): for `envText` = `"SANDESH_PROJECT=Demo\nSANDESH_ADDRESS=Mainline - Demo\n"`
  and `env = {}` → `["SANDESH_ADDRESS","SANDESH_PROJECT"]`; with `env.SANDESH_ADDRESS = "Mainline - Demo"`
  → `["SANDESH_PROJECT"]`; with both set → `[]`; with `env.SANDESH_PROJECT = ""` (and ADDRESS set) →
  `["SANDESH_PROJECT"]`; `"export SANDESH_ADDRESS=x"` counts; `"# SANDESH_ADDRESS=x"`, `"XSANDESH_ADDRESS=x"`
  and `"SANDESH_ADDRESSES=x"` do not; `""` → `[]`.
- [ ] **AC2** — `unexportedIdentityNotice(["SANDESH_ADDRESS","SANDESH_PROJECT"])` equals the §S1 string
  byte-for-byte, and contains `./.env`, `direnv allow`, and both key names.
- [ ] **AC3** — integration (production path, through the registered `session_start` handler with a fake
  `ctx` whose `cwd` is a temp dir holding a `.env` that assigns both keys, both vars unset): exactly one
  `notify` call has level `"warning"` and text `unexportedIdentityNotice(["SANDESH_ADDRESS","SANDESH_PROJECT"])`;
  it fires even when the CLI probe fails (missing-CLI path); afterwards `process.env.SANDESH_ADDRESS` and
  `process.env.SANDESH_PROJECT` are still unset.
- [ ] **AC4** — silence: no nudge when (a) both vars are set, (b) `<cwd>/.env` does not exist, (c) `.env`
  assigns neither key, (d) `ctx.cwd` is `undefined`, (e) `.env` is a directory (unreadable), (f) the only `.env` is in the PARENT of `cwd`
  (no walk-up) — and
  `session_start` resolves without throwing in every case.
- [ ] **AC5** — `SANDESH_AUTOSTART=1` with both vars unset and a `.env` assigning them: both the existing
  `AUTOSTART_ENV_NOTICE` warning and the nudge are emitted, and no watcher is started (no `exec` call whose
  args include `notify`).
- [ ] **AC6** — caller existence: `grep -n "unexportedIdentityKeys(" integrations/pi/src/index.ts` returns ≥ 1
  line; no file under `integrations/pi/src/` other than tests writes `process.env.SANDESH_` (grep).
- [ ] **AC7** — docs, pinned in `integrations/pi/src/docs.test.ts`: `USER_GUIDE.md` §"For Pi extension users"
  contains `direnv allow`, `dotenv`, `direnv hook fish`, `direnv hook bash`, `not exported` (the session-start
  warning is described) and no longer contains `≤ 6-line`;
  `integrations/pi/README.md` contains `direnv` and `.env`. Full `bun test` green; python suite green
  (`tests/test_user_guide.py` unaffected).

## Estimated size

Small — one ~30-line module, a 6-line hook in `session_start`, two doc paragraphs, one test file + doc pins.

## Risk

- Existing `session_start` tests build a fake `ctx` without `cwd`; AC4(d) makes that path a silent no-op,
  so they stay green unchanged.
- A `.env` holding the identity under a different mechanism (e.g. `SANDESH_ADDRESS=${VAR}`) still counts as
  "assigned" — acceptable: the nudge names the key, not the value.

## Non-goals

- Parsing, exporting, or otherwise loading `.env` values; walking up from `<cwd>`; reading `.envrc`.
- Any non-`SANDESH_*` key; `SANDESH_AUTOSTART`/`SANDESH_BIN` in `.env` (not identity).
- CLI, MCP, or wake-supervision changes; emitting `.envrc` (Model B's `modelb-axi init`).
