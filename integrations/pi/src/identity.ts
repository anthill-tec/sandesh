/**
 * Unexported-identity detection (CR-SAN-051 §S1 — PRD-axi-toon.md §4.6 v1.2).
 *
 * The extension keys off `$SANDESH_ADDRESS`/`$SANDESH_PROJECT` in the process
 * environment only; loading a project's `.env` is the shell's job (direnv).
 * These pure helpers let `session_start` say *why* there is no identity when
 * `./.env` assigns it but the shell never exported it. `.env` values are never
 * read, parsed, or returned — only whether a key is assigned.
 */

/** The identity variables the extension reads from the environment (§S1). */
export const IDENTITY_KEYS = ["SANDESH_ADDRESS", "SANDESH_PROJECT"] as const;

/**
 * Keys (in {@link IDENTITY_KEYS} order) that `envText` assigns but `env` has
 * unset or empty (CR-SAN-051 §S1). A line assigns `K` iff it matches
 * `^\s*(?:export\s+)?K\s*=`, so commented or prefixed/suffixed names don't count.
 */
export function unexportedIdentityKeys(envText: string, env: Record<string, string | undefined>): string[] {
  return IDENTITY_KEYS.filter(
    (key) => !env[key] && new RegExp(`^\\s*(?:export\\s+)?${key}\\s*=`, "m").test(envText),
  );
}

/** The one-line session-start warning naming the unexported `keys` (CR-SAN-051 §S1). */
export function unexportedIdentityNotice(keys: string[]): string {
  return (
    `Sandesh identity found in ./.env but not exported: ${keys.join(", ")}. ` +
    "Load it at the shell (direnv: an .envrc containing `dotenv`, then `direnv allow`) and restart pi — " +
    "until then there is no ambient status and no wake."
  );
}
