import { defineConfig } from "vitest/config";
import path from "node:path";

// `import.meta.dirname`, not `__dirname`: Vitest 5 warns that `__dirname` in a
// config file is unsupported by `configLoader: "native"`, which is planned to
// become Vite's default. The plan's draft used `__dirname` and the warning
// appeared on every run.
export default defineConfig({
  test: { environment: "node", include: ["__tests__/**/*.test.ts"] },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
});
