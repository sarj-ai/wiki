import vitest from "@vitest/eslint-plugin";
import type { Linter } from "eslint";
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier/flat";
import importX from "eslint-plugin-import-x";
import testingLibrary from "eslint-plugin-testing-library";
import unicorn from "eslint-plugin-unicorn";
import tseslint from "typescript-eslint";

import sarjStrict from "./eslint.strict.mjs";
import wikiOwned from "./scripts/eslint-owned/index.ts";

const { dirname: tsconfigRootDir } = import.meta;
const { "flat/react": testingLibraryReact } = testingLibrary.configs;

const rulesAsErrors = (rules: Partial<Linter.RulesRecord> = {}): Linter.RulesRecord => {
  const result: Linter.RulesRecord = {};
  for (const [name, setting] of Object.entries(rules)) {
    if (setting === undefined) continue;
    if (setting === "off" || setting === 0) result[name] = setting;
    else if (Array.isArray(setting)) result[name] = ["error", ...setting.slice(1)];
    else result[name] = "error";
  }
  return result;
};

const configsAsErrors = (configs: readonly Linter.Config[]): Linter.Config[] =>
  configs.map((config) => ({
    ...config,
    rules: rulesAsErrors(config.rules),
  }));

// Use the strict config's compatibility-wrapped React plugin to avoid duplicate namespaces.
const withoutReactPlugin = (configs: readonly Linter.Config[]): Linter.Config[] =>
  configs.map((config) => {
    if (!config.plugins || !("react" in config.plugins)) return config;
    const { react: _react, ...plugins } = config.plugins;
    return { ...config, plugins };
  });

const strictNamingConvention = sarjStrict
  .map((config) => config.rules?.["@typescript-eslint/naming-convention"])
  .find((setting): setting is [Linter.Severity, ...unknown[]] => Array.isArray(setting));
const reactNamingConvention: Linter.RuleEntry | undefined = strictNamingConvention && [
  strictNamingConvention[0],
  { format: ["camelCase", "PascalCase"], selector: "function" },
  ...strictNamingConvention.slice(1),
];

export default defineConfig([
  ...withoutReactPlugin(configsAsErrors(nextVitals)),
  ...configsAsErrors(nextTs),
  ...sarjStrict,
  // Type-aware linting: turn on the typed rule sets across the project.
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir,
      },
    },
  },
  ...configsAsErrors(tseslint.configs.strictTypeChecked),
  ...configsAsErrors(tseslint.configs.stylisticTypeChecked),
  // Keep Prettier last among broad configs while allowing scoped overrides below.
  prettier,
  {
    rules: {
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
      // Permit predictable number and boolean interpolation while rejecting nullish and any values.
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowBoolean: true, allowNumber: true },
      ],
      "@typescript-eslint/switch-exhaustiveness-check": "error",
      "array-callback-return": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-await-in-loop": "error",
      "no-console": ["error", { allow: ["warn", "error"] }],
      // Five core rules called out in the rollout plan.
      "no-constant-binary-expression": "error",
      "no-promise-executor-return": "error",
      "no-self-compare": "error",
      "no-unreachable-loop": "error",
      "no-var": "error",
      "object-shorthand": ["error", "always"],
      "prefer-const": "error",
      "prefer-template": "error",
      "require-atomic-updates": "error",
    },
  },
  {
    rules: {
      // Preserve canonical restrictions and let the React Compiler own memoization.
      "no-restricted-syntax": [
        "error",
        {
          message: "Prefer a for-of loop over forEach.",
          selector: "CallExpression[callee.property.name='forEach']",
        },
        {
          message: "Use ES modules instead of namespaces.",
          selector: "TSModuleDeclaration[kind='namespace']",
        },
        {
          message: "Don't memoize by hand — the React Compiler handles it. Remove useCallback.",
          selector: "CallExpression[callee.name='useCallback']",
        },
        {
          message:
            "Don't memoize by hand — the React Compiler handles it. Remove useMemo (extract a plain function or compute inline).",
          selector: "CallExpression[callee.name='useMemo']",
        },
      ],
    },
  },
  // Keep the eight independently tested Wiki correctness rules explicit.
  {
    plugins: { "wiki-owned": wikiOwned },
    rules: {
      "wiki-owned/no-all-duplicated-branches": "error",
      "wiki-owned/no-collapsible-if": "error",
      "wiki-owned/no-element-overwrite": "error",
      "wiki-owned/no-identical-expressions": "error",
      "wiki-owned/no-identical-functions": "error",
      "wiki-owned/no-redundant-jump": "error",
      "wiki-owned/no-use-of-empty-return-value": "error",
      "wiki-owned/prefer-immediate-return": "error",
    },
  },
  {
    plugins: { unicorn },
    rules: {
      "unicorn/error-message": "error",
      // Exempt Next.js bracketed dynamic route directories from filename casing.
      "unicorn/filename-case": [
        "error",
        { cases: { kebabCase: true, pascalCase: true }, ignore: [String.raw`^\[.*\]$`] },
      ],
      "unicorn/no-instanceof-builtins": "error",
      "unicorn/no-useless-undefined": "error",
      "unicorn/prefer-array-flat": "error",
      "unicorn/prefer-array-flat-map": "error",
      "unicorn/prefer-array-some": "error",
      "unicorn/prefer-includes": "error",
      "unicorn/prefer-node-protocol": "error",
      "unicorn/prefer-string-replace-all": "error",
      "unicorn/prefer-top-level-await": "error",
      "unicorn/throw-new-error": "error",
    },
  },
  {
    plugins: { "import-x": importX },
    rules: {
      "import-x/no-cycle": "error",
      "import-x/no-self-import": "error",
    },
    settings: { "import-x/resolver": { typescript: true } },
  },
  {
    rules: {
      "jsx-a11y/click-events-have-key-events": "error",
      "jsx-a11y/label-has-associated-control": "error",
      "jsx-a11y/no-noninteractive-element-interactions": "error",
      "jsx-a11y/no-static-element-interactions": "error",
    },
  },
  // Test-file-only configs.
  {
    files: ["scripts/*.test.ts"],
    rules: {
      // The native Node runner owns registration and failure propagation.
      "@typescript-eslint/no-floating-promises": [
        "error",
        {
          allowForKnownSafeCalls: [
            { from: "package", name: ["describe", "it"], package: "node:test" },
          ],
          ignoreVoid: false,
        },
      ],
    },
  },
  {
    files: ["src/**/*.test.{ts,tsx}"],
    rules: rulesAsErrors(vitest.configs.recommended.rules),
    settings: { vitest: { typecheck: true } },
  },
  {
    files: ["src/**/*.test.tsx"],
    ...testingLibraryReact,
    rules: rulesAsErrors(testingLibraryReact.rules),
  },
  // Restore async and scoping rules that test-double type assertions do not require disabling.
  {
    files: ["src/**/*.test.{ts,tsx}"],
    rules: {
      "@typescript-eslint/promise-function-async": "error",
      "@typescript-eslint/require-await": "error",
      "no-await-in-loop": "error",
      "unicorn/consistent-function-scoping": "error",
    },
  },
  {
    files: ["**/*.tsx"],
    rules: {
      "@typescript-eslint/naming-convention": reactNamingConvention,
    },
  },
  // Require outbound HTTP to use the repository's *-client.ts modules.
  {
    rules: {
      // Its sibling @sarj/no-storage-in-stateless-modules stays unwired on purpose: this repo declares no stateless-module list, so the rule would be a no-op.
      "@sarj/no-raw-fetch-outside-clients": "error",
    },
  },
  // Allow environment access in startup configuration and the validated environment module.
  {
    files: ["next.config.ts", "src/lib/env.ts"],
    rules: {
      "@sarj/no-raw-env": "off",
    },
  },
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    ".vitest/**",
    ".open-next/**",
    "next-env.d.ts",
    // Canonical generated Standards artifact; drift is checked byte-for-byte.
    "eslint.strict.mjs",
    // Codegen output (openapi-ts) — regenerated, never hand-edited.
    "src/lib/generated/**",
  ]),
]);
