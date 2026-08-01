import vitest from "@vitest/eslint-plugin";
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier/flat";
import importX from "eslint-plugin-import-x";
import sonarjs from "eslint-plugin-sonarjs";
import testingLibrary from "eslint-plugin-testing-library";
import unicorn from "eslint-plugin-unicorn";
import tseslint from "typescript-eslint";

import sarjStrict from "./eslint.strict.mjs";

const { dirname: tsconfigRootDir } = import.meta;
const { "flat/react": testingLibraryReact } = testingLibrary.configs;

const rulesAsErrors = (rules = {}) =>
  Object.fromEntries(
    Object.entries(rules).map(([name, setting]) => {
      if (setting === "off" || setting === 0) return [name, setting];
      if (Array.isArray(setting)) return [name, ["error", ...setting.slice(1)]];
      return [name, "error"];
    }),
  );

const configsAsErrors = (configs) =>
  configs.map((config) => ({
    ...config,
    rules: rulesAsErrors(config.rules),
  }));

// eslint.strict.mjs registers `react` wrapped in @eslint/compat's
// fixupPluginRules (eslint-plugin-react 7.37.5 still uses rule-context APIs
// ESLint 10 removed). eslint-config-next registers the same package unwrapped;
// two different objects for one namespace is a hard ConfigError. Drop next's
// definition so every react/* rule resolves against the fixed-up instance.
const withoutReactPlugin = (configs) =>
  configs.map((config) => {
    if (!config.plugins || !("react" in config.plugins)) return config;
    const { react: _react, ...plugins } = config.plugins;
    return { ...config, plugins };
  });

const strictNamingConvention = sarjStrict
  .map((config) => config.rules?.["@typescript-eslint/naming-convention"])
  .find(Array.isArray);
const reactNamingConvention = strictNamingConvention?.map((setting) =>
  typeof setting === "object" && setting?.selector === "function"
    ? { ...setting, format: ["camelCase", "PascalCase"] }
    : setting,
);

export default defineConfig([
  ...withoutReactPlugin(configsAsErrors(nextVitals)),
  ...configsAsErrors(nextTs),
  ...configsAsErrors(sarjStrict),
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
  // Disables ESLint rules that conflict with Prettier. Must come last among
  // the broad config sets — scoped overrides below it still win.
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
      // strict-type-checked sets allowNumber:false, which fires on every
      // dimension/percent we interpolate into a className or inline style.
      // Numbers and booleans coerce predictably; nullish/any are still bad.
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
      // Replaces (and must therefore restate) the canonical eslint.strict.mjs
      // selectors, plus the useCallback/useMemo ban that config shipped before
      // 9.5.0 dropped it without a replacement note. This repo compiles with
      // the React Compiler (next.config.ts `reactCompiler: true`), which
      // memoizes automatically — hand-written useCallback/useMemo is dead
      // weight the compiler has to work around, so the ban stays local until
      // the canonical config restores it.
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
  // sonarjs threshold rules (cognitive-complexity, no-duplicate-string) are
  // deliberately absent: complexity/duplication thresholds are vetoed org-wide
  // as code-smell heuristics, not correctness rules — same rationale as the
  // C901/PLR091x block in backend/.ruff-strict.toml.
  {
    plugins: { sonarjs },
    rules: {
      "sonarjs/no-all-duplicated-branches": "error",
      "sonarjs/no-collapsible-if": "error",
      "sonarjs/no-element-overwrite": "error",
      "sonarjs/no-identical-expressions": "error",
      "sonarjs/no-identical-functions": "error",
      "sonarjs/no-redundant-jump": "error",
      "sonarjs/no-use-of-empty-return-value": "error",
      "sonarjs/prefer-immediate-return": "error",
    },
  },
  {
    plugins: { unicorn },
    rules: {
      "unicorn/error-message": "error",
      // eslint-plugin-unicorn 65 extended filename-case to lint directory
      // segments; exempt Next.js dynamic route dirs (e.g. `[driveId]`), whose
      // bracketed camelCase names are a framework convention, not a style choice.
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
    files: ["**/*.test.{ts,tsx}"],
    plugins: { vitest },
    rules: rulesAsErrors(vitest.configs.recommended.rules),
    settings: { vitest: { typecheck: true } },
  },
  {
    files: ["**/*.test.tsx"],
    ...testingLibraryReact,
    rules: rulesAsErrors(testingLibraryReact.rules),
  },
  // The canonical eslint.strict.mjs test override justifies itself with type
  // assertions (test doubles crossing type boundaries) but also switches off
  // four async/scoping rules that justification does not cover. Re-enable
  // those four for tests; the assertion rules stay relaxed as documented.
  {
    files: ["**/*.test.{ts,tsx}"],
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
  // Config-dependent Standards rule, inert unless wired per-repo: outbound
  // HTTP goes through src/lib/api-client.ts (matched by the rule's default
  // `*-client.ts` allow pattern). Its sibling
  // `@sarj/no-storage-in-stateless-modules` stays unwired on purpose: it is a
  // no-op without a declared stateless-module list, and this repo has none —
  // the backend owns all storage, and the Postgres overlay there is
  // droppable-by-design rather than stateless.
  {
    rules: {
      "@sarj/no-raw-fetch-outside-clients": "error",
    },
  },
  // next.config.ts evaluates before src/ modules can load, and env.ts IS
  // the validated env module the rule points at.
  {
    files: ["next.config.ts", "src/lib/env.ts"],
    rules: {
      "@sarj/no-raw-env": "off",
    },
  },
  // Config files live outside the typed project — disable type-aware rules.
  {
    extends: [tseslint.configs.disableTypeChecked],
    files: ["**/*.config.{ts,mjs,js}", "**/*.mjs"],
  },
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    ".open-next/**",
    "next-env.d.ts",
    // Canonical generated Standards artifact; drift is checked byte-for-byte.
    "eslint.strict.mjs",
    // Codegen output (openapi-ts) — regenerated, never hand-edited.
    "src/lib/generated/**",
    // Copied shadcn primitives track upstream and are formatted, not linted.
    "src/components/ui/**",
  ]),
]);
