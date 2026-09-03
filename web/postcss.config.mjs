// Named rather than an anonymous object literal: the Next lint config enables
// `import/no-anonymous-default-export`, which warns on the plan's one-liner.
const config = { plugins: { "@tailwindcss/postcss": {} } };

export default config;
