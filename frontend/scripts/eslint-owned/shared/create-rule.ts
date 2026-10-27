import { ESLintUtils } from "@typescript-eslint/utils";

export interface OwnedRuleDocs {
  requiresTypeChecking: boolean;
}

export const createRule = ESLintUtils.RuleCreator<OwnedRuleDocs>(
  (name) => `https://github.com/sarj-ai/wiki/blob/main/README.md#${name}`,
);
