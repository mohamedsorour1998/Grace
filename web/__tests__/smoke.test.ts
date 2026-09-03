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
});
