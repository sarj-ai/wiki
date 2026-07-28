import sarj from "@sarj/eslint-plugin";
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

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Type-aware linting: turn on the typed rule sets across the project.
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
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
      "no-console": ["warn", { allow: ["warn", "error"] }],
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
    plugins: { sonarjs },
    rules: {
      "sonarjs/cognitive-complexity": ["error", 15],
      "sonarjs/no-all-duplicated-branches": "error",
      "sonarjs/no-collapsible-if": "error",
      "sonarjs/no-duplicate-string": ["error", { threshold: 5 }],
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
      "jsx-a11y/click-events-have-key-events": "warn",
      "jsx-a11y/label-has-associated-control": "warn",
      "jsx-a11y/no-noninteractive-element-interactions": "warn",
      "jsx-a11y/no-static-element-interactions": "warn",
    },
  },
  // Rule set sourced from the plugin so new @sarj rules are picked up
  // automatically. Every rule is an error; scoped overrides below handle
  // framework-owned files and this repo's env source of truth.
  {
    plugins: { "@sarj": sarj },
    rules: Object.fromEntries(Object.keys(sarj.rules).map((name) => [`@sarj/${name}`, "error"])),
  },
  // Test-file-only configs.
  {
    files: ["**/*.test.{ts,tsx}"],
    plugins: { vitest },
    rules: vitest.configs.recommended.rules,
    settings: { vitest: { typecheck: true } },
  },
  {
    files: ["**/*.test.tsx"],
    ...testingLibrary.configs["flat/react"],
  },
  // next.config.ts evaluates before src/ modules can load, and env.ts IS
  // the validated env module the rule points at.
  {
    files: ["next.config.ts", "src/lib/env.ts"],
    rules: {
      "@sarj/no-raw-env": "off",
    },
  },
  {
    files: ["src/components/wiki-sidebar/drive-section.tsx"],
    rules: { "sonarjs/cognitive-complexity": "off" },
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
    // Codegen output (openapi-ts) — regenerated, never hand-edited.
    "src/lib/generated/**",
    // Copied shadcn primitives track upstream and are formatted, not linted.
    "src/components/ui/**",
  ]),
]);

export default eslintConfig;
