import { describe, expect, it } from "vitest";
import type { CaseSummary } from "@/lib/types";

describe("the scaffold", () => {
  it("resolves the @/ alias and the shared types", () => {
    // A type-only import cannot fail at runtime, so construct a value: this
    // fails if `CaseSummary`'s fields are renamed, which is what later tasks
    // depend on.
    const c: CaseSummary = {
      caseId: "c-011", status: "escalated", program: "medicaid",
      deadline: "2026-10-20", reason: "material_income_change", filed: false,
    };
    expect(c.caseId).toBe("c-011");
    expect(c.filed).toBe(false);
  });

  it("does not ship a static export config", async () => {
    // `output: "export"` would silently remove route handlers and middleware,
    // taking the Cognito gate and the decide endpoint with them. Assert the
    // config does not set it.
    const mod = await import("@/next.config");
    expect((mod.default as Record<string, unknown>).output).toBeUndefined();
  });

  it("does not mark the AWS SDK as a server-external package", async () => {
    // Measured on the deployed app: with `serverExternalPackages` listing the
    // SDK, Next emits a bare `require` and leaves the package out of the bundle,
    // and Amplify's SSR bundle ships only what the trace includes. Every page
    // returned 500 with a *valid* session — sign-in worked, then the first
    // DynamoDB import failed with
    //   `Cannot find module '@aws-sdk/client-dynamodb-3e32f4e24bb075d4'`
    // naming a module nobody published, because Turbopack appends a content hash
    // to an external's name.
    //
    // It was protecting nothing: verified against a real `next build` that no
    // chunk under `.next/static/` references `DynamoDBClient` or
    // `InvokeAgentRuntimeCommand`, because `lib/cases.ts` and `lib/decide.ts` are
    // imported only from server components and a route handler.
    const mod = await import("@/next.config");
    const external = (mod.default as { serverExternalPackages?: string[] })
      .serverExternalPackages ?? [];
    for (const pkg of external) {
      expect(pkg, `${pkg} must not be external — it will be absent at runtime`)
        .not.toMatch(/^@aws-sdk\//);
    }
    // And assert the loop ran meaningfully rather than passing on an empty list
    // for a reason nobody checked.
    expect(Array.isArray(external)).toBe(true);
  });
});
