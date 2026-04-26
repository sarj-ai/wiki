import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier/flat";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Disables ESLint rules that conflict with Prettier. Must come last.
  prettier,
  {
    rules: {
      // Additional strictness beyond next-ts defaults.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      // Allow @ts-nocheck WHEN it carries a description — used to mark files
      // scheduled for deletion in Sub-PR D of the Navigator pivot.
      "@typescript-eslint/ban-ts-comment": [
        "error",
        {
          "ts-nocheck": "allow-with-description",
          minimumDescriptionLength: 10,
        },
      ],
      "no-console": ["warn", { allow: ["warn", "error"] }],
      eqeqeq: ["error", "always", { null: "ignore" }],
      "prefer-const": "error",
      "no-var": "error",
    },
  },
  // Relax React 19's react-hooks/purity rule inside shadcn primitives we
  // don't own — upstream shadcn uses Math.random() in skeleton widths, etc.
  {
    files: ["src/components/ui/**"],
    rules: {
      "react-hooks/purity": "off",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    ".open-next/**",
    "next-env.d.ts",
    // Components, hooks, and routes scheduled for deletion in Sub-PR D of
    // the Navigator pivot (see PIVOT_PLAN.md §12). Linting them is wasted
    // effort — they're going away wholesale. Removed from the ignore list
    // at the end of Sub-PR D.
    "src/app/(app)/**",
    "src/components/document/**",
    "src/components/import/**",
    "src/components/permissions/**",
    "src/components/search/**",
    "src/components/sidebar/**",
    "src/hooks/use-collapsed-sections.ts",
    "src/hooks/use-document-data.ts",
    "src/hooks/use-editor-preference.ts",
    "src/hooks/use-page-operations.ts",
    "src/hooks/use-page-tree.ts",
    "src/hooks/use-starred-pages.ts",
    "src/hooks/use-workspaces.ts",
    "src/hooks/use-workspace-management.ts",
    "e2e/**",
  ]),
]);

export default eslintConfig;
