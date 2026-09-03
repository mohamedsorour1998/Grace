import next from "eslint-config-next";

// `...next`, not `...next()`. `eslint-config-next@16.3.4` exports
// `Linter.Config[]` — an array, verified from its own `dist/index.d.ts`
// (`declare const config: Linter.Config[]; export = config`). The plan's draft
// spread a call, which throws `next is not a function`.
//
// Named, not anonymous: `import/no-anonymous-default-export` is one of the
// rules this very config turns on, so an anonymous export lints itself.
const config = [...next, { ignores: [".next/**", "node_modules/**"] }];

export default config;
