// TEMPORARY — Step 6 of Task 6. Deleted after the run.
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "node",
    include: ["__live__/**/*.test.ts"],
    testTimeout: 120_000,
    // Without these the console output from a live read is swallowed.
    disableConsoleIntercept: true,
    reporters: ["verbose"],
  },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
});
