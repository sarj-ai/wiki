import eslintComments from "@eslint-community/eslint-plugin-eslint-comments";
import betterTailwindcss from "eslint-plugin-better-tailwindcss";
import perfectionist from "eslint-plugin-perfectionist";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import simpleImportSort from "eslint-plugin-simple-import-sort";
import unicorn from "eslint-plugin-unicorn";
import zod from "eslint-plugin-zod";
import tseslint from "typescript-eslint";

import sarj from "./eslint-plugin-latest.mjs";

/** @type {import("eslint").Linter.Config[]} */
const config = [
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,

  {
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        projectService: true,
        tsconfigRootDir: process.cwd(),
      },
    },
    // Dead eslint-disable directives are an error (parity with ruff RUF100).
    linterOptions: {
      reportUnusedDisableDirectives: "error",
    },
    plugins: {
      "@eslint-community/eslint-comments": eslintComments,
      "@sarj": sarj,
      "@typescript-eslint": tseslint.plugin,
      perfectionist,
      react,
      "react-hooks": reactHooks,
      "simple-import-sort": simpleImportSort,
      unicorn,
      zod,
    },
    rules: {
      "@eslint-community/eslint-comments/no-restricted-disable": [
        "warn",
        "no-console",
        "react-hooks/exhaustive-deps",
      ],
      // ...and a suppression must name the rule it suppresses. A bare
      // `/* eslint-disable */` at the top of a file silently switches off EVERY
      // rule for the whole file — including ones added later — which is the
      // file-level-suppression escape hatch flagged repeatedly in review.
      "@eslint-community/eslint-comments/no-unlimited-disable": "error",
      // strictTypeChecked) covers `@ts-expect-error`, so a bespoke rule would be
      // a duplicate.
      "@eslint-community/eslint-comments/require-description": ["error", { ignore: [] }],
      "@sarj/enforce-file-structure": "error",
      "@sarj/jsdoc-restates-signature": "error",
      "@sarj/no-client-side-data-fetching": "error",
      "@sarj/no-comment-cruft": "error",
      "@sarj/no-cors-wildcard-with-credentials": "error",
      "@sarj/no-dynamic-sql": "error",
      "@sarj/no-enum": "error",
      "@sarj/no-fat-try-blocks": "error",
      "@sarj/no-insecure-random-id": "error",
      "@sarj/no-json-stringify-error": "error",
      "@sarj/no-log-only-catch": "error",
      "@sarj/no-offset-pagination": "error",
      // High-volume/stylistic, so warn — same treatment as prefer-semantic-colors
      // and prefer-string-literal-union above. Measured on a 1,578-file
      // third-party corpus: every hit was the conventional `[value, cursor]`
      // parser idiom, i.e. style rather than defect.
      "@sarj/no-positional-tuple-return": "error",
      "@sarj/no-raw-env": "error",
      "@sarj/no-repeated-string-literal": "error",
      // ── anti-comment-verbosity family (2026-07) ─────────────────────────────
      // From a 37,918-comment, nine-repo measurement study. All three are
      // deletion-class, so each was validated against pydantic / trio / attrs as
      // well as the maintained repos: `no-restated-comment` fires 0 times in
      // bulbul and 4 times across the three famous corpora combined (every one a
      // genuine `// set_inheritable` over `s1.set_inheritable(False)`);
      // `trailing-value-narration` 18 hits, 18 true positives, all `staleTime`
      // and cookie-age lines; `jsdoc-restates-signature` 36 hits and 0 measured
      // false positives, and it offers a SUGGESTION rather than a `--fix`
      // because deleting a doc block in bulk is silent information loss if the
      // judgement is wrong even once.
      "@sarj/no-restated-comment": "error",
      "@sarj/no-secret-in-log": "error",
      "@sarj/no-select-star": "error",
      "@sarj/no-sentinel-return-on-catch": "error",
      "@sarj/no-sequential-await": "error",
      "@sarj/no-silent-promise-catch": "error",
      "@sarj/no-sleep-in-test-body": "error",

      "@sarj/no-string-concat-in-loop": "error",
      // An assertion whose operands are all literals can never fail. The TS
      // half of SARJ057; the Python half is the `sarj-no-tautological-expect`
      // pre-commit hook.
      "@sarj/no-tautological-expect": "error",
      "@sarj/no-unnecessary-use-client": "error",
      // High-volume/stylistic — warn until rollout proves FP rate.
      "@sarj/no-unsafe-cast": "error",
      "@sarj/no-zod-native-enum": "error",
      // ── 2.8.0 / 2.9.0 additions ─────────────────────────────────────────────
      // Correctness and security invariants — error, like their peers above.
      "@sarj/prefer-constant-time-secret-compare": "error",
      "@sarj/prefer-discriminated-union": "error",
      "@sarj/prefer-module-level-constant": "error",
      "@sarj/prefer-non-nullable-collection": "error",
      "@sarj/prefer-schema-for-api-payload": "error",
      "@sarj/prefer-semantic-colors": ["error", { requireSemanticTokens: true }],
      "@sarj/prefer-server-actions": "error",
      "@sarj/prefer-shadcn": "error",
      "@sarj/prefer-string-literal-union": "error",
      "@sarj/primary-export-file-name": "error",
      "@sarj/require-assert-never": "error",
      // Mined from 2y of PR review feedback + 5-repo code-smell audit (2026-07).
      "@sarj/require-fetch-timeout": "error",
      // Substitutability. An exported class that stores injected collaborators
      // and implements no interface forces every consumer onto the concrete
      // type, so the only way to test a consumer is to mock the class. Measured
      // across 11 first-party repos (7,912 files, 229 exported classes): 82%
      // already carry a port, 29 fire, 28 hand-reviewed as true positives.
      "@sarj/require-interface-for-injected-service": "error",
      "@sarj/require-schema-validate-search": "error",
      "@sarj/require-zod-form-validation": "error",
      "@sarj/single-public-export": "error",
      "@sarj/store-insert-requires-on-conflict": "error",
      "@sarj/trailing-value-narration": "error",

      // The COMPLETE @sarj/eslint-plugin strict ruleset — deliberately not
      // pinned to a version number here. A hand-written "@2.7.0" claim went
      // stale twice and was wrong both times (12 rules unwired at 2.9.0); the
      // sync tests in packages/typescript/tests/strict-config-sync.test.ts now
      // assert completeness and tier parity on every run, which is a guarantee
      // rather than a comment that rots. Tiers mirror the plugin's
      // own `configs.strict` — error for most, warn for the stylistic/high-volume
      // rules (prefer-semantic-colors, prefer-string-literal-union,
      // no-unsafe-cast, which are warn in the plugin's strict too).
      //
      // Deviations from the plugin's strict tiers, and the ONLY ones — the
      // `severities match the plugin's own strict preset` test in
      // packages/typescript/tests/strict-config-sync.test.ts fails on any other:
      //   - enforce-file-structure: warn here, error in the plugin. Structural
      //     moves are high-churn for existing consumers, so it stays advisory.
      //   - no-repeated-string-literal: warn here, error in the plugin. It flags
      //     duplicated structured literals, which lands in bulk on first
      //     adoption; warn until a rollout proves the signal.
      //   - no-storage-in-stateless-modules and no-raw-fetch-outside-clients are
      //     error in the plugin's strict but not enabled here at all; both are
      //     meaningless without per-repo paths. See the opt-in block below.
      //
      // A `files:`-scoped block further down deliberately turns no-raw-env off
      // for env source-of-truth files; that is an override, not a tier change.
      //
      // no-enum / no-fat-try-blocks / no-raw-env are the single owners of the
      // enum / oversized-try / process.env concerns (the native no-restricted-*
      // equivalents were removed above).
      "@sarj/zod-naming-convention": "error",
      "@typescript-eslint/array-type": "error",
      "@typescript-eslint/await-thenable": "error",
      "@typescript-eslint/consistent-indexed-object-style": ["error", "record"],
      "@typescript-eslint/consistent-type-assertions": [
        "error",
        {
          assertionStyle: "never",
        },
      ],
      "@typescript-eslint/consistent-type-imports": [
        "error",
        {
          fixStyle: "inline-type-imports",
          prefer: "type-imports",
        },
      ],
      "@typescript-eslint/naming-convention": [
        "error",
        {
          format: ["camelCase"],
          leadingUnderscore: "allow",
          selector: "default",
        },
        {
          format: ["camelCase", "UPPER_CASE", "PascalCase"],
          leadingUnderscore: "allow",
          selector: "variable",
        },
        { format: ["PascalCase"], selector: "typeLike" },
        {
          format: ["camelCase", "PascalCase", "UPPER_CASE"],
          selector: "import",
        },
        { format: null, selector: "objectLiteralProperty" },
        { format: null, selector: "typeProperty" },
        {
          format: ["camelCase", "snake_case"],
          leadingUnderscore: "allow",
          selector: "parameter",
        },
      ],
      "@typescript-eslint/no-base-to-string": "error",
      "@typescript-eslint/no-confusing-non-null-assertion": "error",
      "@typescript-eslint/no-deprecated": "error",
      "@typescript-eslint/no-duplicate-type-constituents": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-import-type-side-effects": "error",
      "@typescript-eslint/no-invalid-void-type": "error",
      "@typescript-eslint/no-meaningless-void-operator": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/no-misused-spread": "error",
      "@typescript-eslint/no-mixed-enums": "error",
      "@typescript-eslint/no-non-null-asserted-optional-chain": "error",

      "@typescript-eslint/no-non-null-assertion": "error",
      "@typescript-eslint/no-redundant-type-constituents": "error",
      "@typescript-eslint/no-shadow": "error",
      "@typescript-eslint/no-unnecessary-condition": "error",
      "@typescript-eslint/no-unnecessary-template-expression": "error",
      "@typescript-eslint/no-unnecessary-type-assertion": "error",
      "@typescript-eslint/no-unnecessary-type-conversion": "error",
      "@typescript-eslint/no-unsafe-argument": "error",
      "@typescript-eslint/no-unsafe-assignment": "error",
      "@typescript-eslint/no-unsafe-call": "error",
      "@typescript-eslint/no-unsafe-enum-comparison": "error",
      "@typescript-eslint/no-unsafe-member-access": "error",
      "@typescript-eslint/no-unsafe-return": "error",

      "@typescript-eslint/no-unsafe-type-assertion": "error",

      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          ignoreRestSiblings: true,
          varsIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/only-throw-error": [
        "error",
        {
          allow: [
            {
              from: "package",
              name: ["redirect"],
              package: "@tanstack/react-router",
            },
          ],
        },
      ],
      // Additional type-aware strictness incorporated from bulbul's base config.
      "@typescript-eslint/prefer-as-const": "error",
      "@typescript-eslint/prefer-find": "error",
      "@typescript-eslint/prefer-includes": "error",
      "@typescript-eslint/prefer-nullish-coalescing": "error",
      "@typescript-eslint/prefer-optional-chain": "error",

      // Every suppression must say WHY. `require-description` already covers
      // eslint-disable comments and `@typescript-eslint/ban-ts-comment` (from
      "@typescript-eslint/prefer-promise-reject-errors": "error",
      "@typescript-eslint/prefer-readonly": "error",
      "@typescript-eslint/prefer-string-starts-ends-with": "error",

      "@typescript-eslint/promise-function-async": "error",
      "@typescript-eslint/require-array-sort-compare": "error",

      "@typescript-eslint/require-await": "error",
      "@typescript-eslint/restrict-template-expressions": "error",
      "@typescript-eslint/switch-exhaustiveness-check": "error",
      "array-callback-return": "error",
      eqeqeq: ["error", "always"],
      "no-await-in-loop": "error",
      "no-console": ["error", { allow: ["warn", "error"] }],
      "no-else-return": "error",
      "no-fallthrough": "error",
      "no-param-reassign": "error",
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              importNames: ["auth", "currentUser"],
              message: "Prefer an internal user-service wrapper.",
              name: "@clerk/nextjs",
            },
            {
              message: "Prefer an internal user-service wrapper.",
              name: "@clerk/nextjs/server",
            },
          ],
          patterns: ["*/index", "*/index.ts"],
        },
      ],
      // Dedup: TS-enum ban → @sarj/no-enum, oversized-try-block ban →
      // @sarj/no-fat-try-blocks, and process.env ban → @sarj/no-raw-env (all
      // added below). Only the selectors WITHOUT a @sarj equivalent stay here,
      // so each concern fires exactly one diagnostic.
      "no-restricted-syntax": [
        "error",
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
      "no-return-await": "error",

      "no-shadow": "off",
      "no-var": "error",
      "object-shorthand": ["error", "always"],
      "perfectionist/sort-classes": "error",
      "perfectionist/sort-interfaces": "error",
      "perfectionist/sort-jsx-props": "error",
      // Deterministic ordering (incorporated from bulbul). perfectionist sorts
      // structural members; simple-import-sort owns import/export ordering
      // (chosen over eslint-plugin-import to avoid Next.js resolver conflicts).
      "perfectionist/sort-objects": ["error", { order: "asc", type: "natural" }],
      "perfectionist/sort-union-types": "error",
      "prefer-const": "error",
      "prefer-template": "error",
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/rules-of-hooks": "error",
      "react/forbid-component-props": [
        "error",
        {
          forbid: [
            {
              message:
                "Use design-token utility classes. For dynamic values, set a CSS custom property and reference it via an arbitrary-value class.",
              propName: "style",
            },
          ],
        },
      ],
      "react/forbid-dom-props": [
        "error",
        {
          forbid: [
            {
              message:
                "Use design-token utility classes. For dynamic values, set a CSS custom property and reference it via an arbitrary-value class.",
              propName: "style",
            },
          ],
        },
      ],
      "react/forbid-elements": [
        "error",
        {
          forbid: [
            {
              element: "button",
              message: "Use <Button> from your design system.",
            },
            {
              element: "input",
              message: "Use <Input> / <Checkbox> / <RadioGroup> from your design system.",
            },
            {
              element: "select",
              message: "Use <Select> from your design system.",
            },
            {
              element: "textarea",
              message: "Use <Textarea> from your design system.",
            },
            {
              element: "dialog",
              message: "Use <Dialog> / <AlertDialog> from your design system.",
            },
            {
              element: "table",
              message: "Use <Table> family from your design system.",
            },
          ],
        },
      ],
      "react/jsx-boolean-value": ["error", "never"],
      "react/jsx-fragments": "error",
      "react/jsx-no-comment-textnodes": "error",
      "react/jsx-no-duplicate-props": "error",
      "react/jsx-no-leaked-render": ["error", { validStrategies: ["ternary", "coerce"] }],
      "react/jsx-no-script-url": "error",
      "react/jsx-no-target-blank": "error",
      "react/jsx-no-undef": "error",
      "react/jsx-no-useless-fragment": "error",
      "react/jsx-pascal-case": "error",
      "react/no-danger": "error",
      "react/no-this-in-sfc": "error",
      "react/no-unstable-nested-components": "error",
      "react/self-closing-comp": "error",
      "react/void-dom-elements-no-children": "error",

      "simple-import-sort/exports": "error",
      "simple-import-sort/imports": "error",
      "unicorn/consistent-function-scoping": "error",
      "unicorn/filename-case": [
        "error",
        { cases: { kebabCase: true }, ignore: [String.raw`\.d\.ts$`] },
      ],
      "unicorn/no-array-for-each": "warn",
      "unicorn/no-await-expression-member": "error",
      "unicorn/no-useless-undefined": "error",
      "unicorn/prefer-logical-operator-over-ternary": "error",
      "unicorn/prefer-node-protocol": "error",
      "unicorn/prefer-string-replace-all": "error",

      "unicorn/prefer-structured-clone": "error",
      "unicorn/prefer-switch": "warn",
      "unicorn/prefer-top-level-await": "error",
      "unicorn/relative-url-style": ["error", "never"],
      "unicorn/throw-new-error": "error",
      "zod/prefer-enum-over-literal-union": "error",

      // Deliberately NOT enabled here — these two are architectural rules that
      // are meaningless without per-repo paths, so a shared config cannot set
      // them. `no-storage-in-stateless-modules` defaults to `modules: []` and is
      // inert until a consumer names its stateless modules;
      // `no-raw-fetch-outside-clients` needs an `allow` list matching that
      // repo's client-layer convention (the default assumes `clients/`). Opt in
      // per repo:
      //   "@sarj/no-storage-in-stateless-modules": ["error", { modules: [...] }],
      //   "@sarj/no-raw-fetch-outside-clients": ["error", { allow: [...] }],
    },
    settings: { react: { version: "detect" } },
  },

  {
    files: ["**/*.test.ts", "**/*.test.tsx", "**/__tests__/**/*"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
    },
  },

  {
    files: ["**/components/ui/**", "**/components/design-system/**"],
    rules: {
      "react/forbid-elements": "off",
    },
  },

  {
    // The env source-of-truth files parse process.env into a Zod-validated
    // object the rest of the app imports; they're the one place raw env access
    // is legitimate, so @sarj/no-raw-env (which replaced the native
    // no-restricted-properties process.env ban) is disabled here.
    files: [
      "**/*.config.{ts,tsx,js,jsx,mjs,cjs,mts,cts}",
      "**/scripts/**",
      "**/env/**",
      "**/env.{ts,tsx,js,mjs}",
      "**/server-env.{ts,tsx,js,mjs}",
      "**/client-env.{ts,tsx,js,mjs}",
      "**/server-settings.{ts,tsx,js,mjs}",
      "**/client-settings.{ts,tsx,js,mjs}",
    ],
    rules: {
      "@sarj/no-raw-env": "off",
    },
  },

  // better-tailwindcss: class-string hygiene for Tailwind repos. Scoped to JSX/TSX
  // (where className strings live) and harmless where no Tailwind classes exist —
  // these three rules only inspect literal class strings, so non-Tailwind repos
  // simply see zero findings. Kept in its own block so the plugin is only wired
  // where it applies.
  {
    files: ["**/*.{jsx,tsx}"],
    plugins: {
      "better-tailwindcss": betterTailwindcss,
    },
    rules: {
      "better-tailwindcss/no-conflicting-classes": "error",
      "better-tailwindcss/no-deprecated-classes": "error",
      "better-tailwindcss/no-duplicate-classes": "error",
    },
  },
];

export default config;
