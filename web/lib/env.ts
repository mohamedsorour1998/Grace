/**
 * Server-only configuration, read once and validated loudly.
 *
 * A missing or blank name fails here with the variable's name in the message,
 * rather than surfacing as `undefined` inside an SDK call three layers down.
 * Plan 2 learned this twice: `os.getenv(name, default)` defaults only on
 * *absence*, so a blank `GRACE_STORE` bypassed its default and would have had a
 * deployed runtime write its ledger to memory and discard it at exit.
 *
 * None of these are `NEXT_PUBLIC_`. A table name or runtime ARN in the client
 * bundle is not a secret exactly, but it is a map of the backend, and nothing
 * in the browser needs it.
 *
 * `runtimeArn` is required even though `lib/cases.ts` never uses it. That is
 * deliberate: this function is the app's single startup check, and a dashboard
 * whose read pages work while its decide route is misconfigured is worse than
 * one that refuses to start — the caseworker would only discover it at the
 * moment they tried to decide.
 */

export interface Env {
  region: string;
  tableName: string;
  escalationIndex: string;
  runtimeArn: string;
}

/** What this function actually needs: something it can look a name up in.
 *
 *  Deliberately NOT `NodeJS.ProcessEnv`, which the plan's draft used. Next 16
 *  declares `NODE_ENV` as a **required** property on that interface
 *  (`next/types/global.d.ts:23`), so `{ GRACE_TABLE_NAME: "x" } as NodeJS.ProcessEnv`
 *  does not compile — `error TS2352: … Property 'NODE_ENV' is missing`. Every
 *  test would have to either invent a `NODE_ENV` it does not care about or cast
 *  through `unknown`, and a double cast is exactly the "the promise stops being
 *  checked" hole Task 2 found in `DecisionAttempt`.
 *
 *  `process.env` is assignable to this, so the default still works. */
export type EnvSource = Readonly<Record<string, string | undefined>>;

function required(source: EnvSource, name: string): string {
  const value = source[name];
  if (value === undefined || value.trim() === "") {
    throw new Error(
      `${name} is not set. The dashboard reads Grace's deployed resources and ` +
      `cannot guess their names.`,
    );
  }
  // Return the TRIMMED value, not the raw one. Checking `value.trim()` and then
  // returning `value` accepts `" grace-cases "` as present and hands the spaces
  // to the SDK, where the failure is a ResourceNotFoundException naming a table
  // that looks correct in the log line.
  return value.trim();
}

export function readEnv(source: EnvSource = process.env): Env {
  return {
    region: source.AWS_REGION?.trim() || "us-east-1",
    tableName: required(source, "GRACE_TABLE_NAME"),
    escalationIndex: required(source, "GRACE_ESCALATION_INDEX"),
    runtimeArn: required(source, "GRACE_RUNTIME_ARN"),
  };
}
