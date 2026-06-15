# CR-SAN-040 — user guide (MCP + Pi sections) + leaner README + `notify --help` epilog

**Status:** PENDING
**Priority:** High (users can't operate the software from the docs today — the *how* is missing)
**Depends on:** —
**Labels:** docs, dx, usability, hotfix-0.3.1
**Wave:** hotfix 0.3.1
**Design reference:** README/`docs/INSTALL.md` current state; `CLAUDE.md` wake mechanism; `notify.py`
EXIT CODES docstring (verified). Plain-language, goal-first — no unexplained internal jargon.

## Context
The README explains *what/why* (the wake constraint, the model) but never the **operational how** —
how a user actually runs cooperating sessions. And the *how differs by surface*: **MCP** users must
background `sandesh notify` themselves (the host wakes them on its exit); **Pi** users get a **native
wake** built into the extension (no manual listener). That handholding lives only in `CLAUDE.md`
(contributor doc) today. Ship a real user guide as a 0.3.1 hotfix.

## Scope
- **§S1 — `docs/USER_GUIDE.md` (new, plain-language, goal-first).** Sections:
  - **Intro:** what Sandesh does for cooperating agent sessions (a coordinator + workers leaving
    each other messages); the wake idea in plain words (an idle session can only be woken by its own
    harness). No "Model-B"/"notifier"/"exit code N" as the *lead* framing — define or avoid jargon.
  - **For MCP users (Claude & MCP clients):** register `sandesh-mcp`; the verbs are tools; to be
    woken, **run the listener in the background** — `sandesh notify --to "<you>"` via the host's
    background-run tool; the loop (register → background listen → on stop, `fetch` → act → `reply` →
    listen again; `send` anytime); a plain **"why the listener stopped"** table (mail / timeout /
    project retired-don't-restart / taken-over).
  - **For Pi extension users:** install via `pi install npm:@anthill-tec/sandesh-pi`; the verbs are
    Pi tools; the extension **wakes the session itself (native wake) — you do NOT run
    `sandesh notify`**; how that behaves. Note Pi needs the `sandesh` CLI (uvx-on-demand / install).
  - **Common:** send/reply/fetch in plain terms; cross-project sending needs an admin grant
    (plain); project archive/retire in one line.
- **§S2 — `sandesh notify --help` epilog (cli.py).** Add a `RawDescriptionHelpFormatter` epilog
  documenting the **stop reasons** (the exit codes, in the plain wording from `notify.py`'s
  docstring) so the CLI is self-documenting.
- **§S3 — leaner README.** Trim to a tight overview: what/why + model + a **prominent "📖 must-read:
  [User Guide](docs/USER_GUIDE.md)"** link near the top; move the verb walk-through detail into the
  guide; drop/condense the verbose Roadmap. **Preserve** the `mcp-name` marker.

## Acceptance criteria
- [ ] **AC1 — guide exists, both surfaces.** `docs/USER_GUIDE.md` exists with a **MCP-users** section
      and a **Pi-extension-users** section; the MCP section tells the reader to run `sandesh notify`
      in the background and describes the listen→fetch→reply→listen loop; the Pi section states the
      extension wakes the session itself (**native wake, no manual `sandesh notify`**).
- [ ] **AC2 — stop-reasons documented.** The guide contains a plain "why the listener stopped" table
      covering mail-arrived, timeout, project-retired (do-not-restart), and taken-over.
- [ ] **AC3 — README leaner + must-read link.** README links `docs/USER_GUIDE.md` as a prominent
      must-read near the top, and is shorter than before (e.g. the verbose Roadmap removed/condensed);
      what/why/model retained.
- [ ] **AC4 — `notify --help` epilog.** `sandesh notify --help` output contains the stop-reason /
      exit-code explanations (assert the help text includes the plain descriptions).
- [ ] **AC5 — guards green.** `test_server_json.py` (mcp-name marker) and `test_pkgbuild.py` stay
      green; full regression unaffected.
- [ ] **AC6 — plain-language gate.** The guide leads each surface section with what the *user does*
      (a `sandesh …` command / `pi install …`) before any internals; assert the guide does NOT use
      the bare term "exit code" as the user-facing heading (stop-reasons are framed plainly).

## Estimated size
Small-medium — one new doc, README trim, a cli.py epilog, doc-marker + help-output tests.

## Risks / open questions
- (none — docs + a help-string; exit codes verified against `notify.py`; no test pins README usage /
  `notify --help` today.)

## Non-goals
- Behaviour changes beyond the help epilog; re-documenting AUR; the bundled MCP `sandesh://usage`
  resource (agent-facing, already covers the wake).
