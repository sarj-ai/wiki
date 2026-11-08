import { defineConfig, type OxlintConfig } from "oxlint";

import { createConfig, ignorePatterns } from "./oxlint.strict.mts";

const managed = createConfig({
  testFrameworks: ["vitest", "node", "testing-library"],
});

const config: OxlintConfig = defineConfig({
  extends: [managed],
  settings: managed.settings ?? {},
  jsPlugins: [
    { name: "wiki-owned", specifier: "./scripts/oxlint-owned/index.ts" },
  ],
  ignorePatterns: [
    ...ignorePatterns,
    ".vitest/**",
    ".open-next/**",
    "next-env.d.ts",
    "src/lib/generated/**",
  ],
  rules: {
    "wiki-owned/no-all-duplicated-branches": "error",
    "wiki-owned/no-collapsible-if": "error",
    "wiki-owned/no-element-overwrite": "error",
    "wiki-owned/no-identical-expressions": "error",
    "wiki-owned/no-identical-functions": "error",
    "wiki-owned/no-redundant-jump": "error",
    "wiki-owned/no-use-of-empty-return-value": "error",
    "wiki-owned/prefer-immediate-return": "error",
    "typescript/consistent-type-imports": [
      "error",
      { fixStyle: "inline-type-imports" },
    ],
    "typescript/no-explicit-any": "error",
    "typescript/no-unused-vars": [
      "error",
      {
        argsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
      },
    ],
    "typescript/restrict-template-expressions": [
      "error",
      { allowBoolean: true, allowNumber: true },
    ],
    "typescript/switch-exhaustiveness-check": "error",
    "array-callback-return": "error",
    eqeqeq: ["error", "always", { null: "ignore" }],
    "no-await-in-loop": "error",
    "no-console": ["error", { allow: ["warn", "error"] }],
    "no-constant-binary-expression": "error",
    "no-promise-executor-return": "error",
    "no-self-compare": "error",
    "no-unreachable-loop": "error",
    "no-var": "error",
    "object-shorthand": ["error", "always"],
    "prefer-const": "error",
    "prefer-template": "error",
    "eslint-js/require-atomic-updates": "error",
    "eslint-js/no-restricted-syntax": [
      "error",
      {
        selector: "CallExpression[callee.property.name='forEach']",
        message: "Prefer a for-of loop over forEach.",
      },
      {
        selector: "TSModuleDeclaration[kind='namespace']",
        message: "Use ES modules instead of namespaces.",
      },
      {
        selector: "CallExpression[callee.name='useCallback']",
        message: "The React Compiler handles memoization. Remove useCallback.",
      },
      {
        selector: "CallExpression[callee.name='useMemo']",
        message:
          "The React Compiler handles memoization. Compute inline or extract a function.",
      },
    ],
    "unicorn/filename-case": [
      "error",
      {
        cases: { kebabCase: true, pascalCase: true },
        ignore: [String.raw`^\[.*\]$`],
      },
    ],
    "import/no-cycle": "error",
    "import/no-self-import": "error",
    "jsx-a11y/click-events-have-key-events": "error",
    "jsx-a11y/label-has-associated-control": "error",
    "jsx-a11y/no-noninteractive-element-interactions": "error",
    "jsx-a11y/no-static-element-interactions": "error",
    "@sarj/no-raw-fetch-outside-clients": "error",
  },
  overrides: [
    {
      files: ["next.config.ts", "src/lib/env.ts"],
      rules: { "@sarj/no-raw-env": "off" },
    },
    {
      files: ["scripts/*.test.ts"],
      rules: {
        "typescript/no-floating-promises": [
          "error",
          {
            allowForKnownSafeCalls: [
              {
                from: "package",
                name: ["describe", "it"],
                package: "node:test",
              },
            ],
            ignoreVoid: false,
          },
        ],
      },
    },
    {
      files: ["src/**/*.test.{ts,tsx}"],
      rules: {
        "typescript/promise-function-async": "error",
        "typescript/require-await": "error",
        "no-await-in-loop": "error",
        "unicorn/consistent-function-scoping": "error",
      },
    },
  ],
});

export default config;
