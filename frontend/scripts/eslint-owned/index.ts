import { fixupPluginRules } from "@eslint/compat";
import type { TSESLint } from "@typescript-eslint/utils";
import type { CompatiblePlugin } from "typescript-eslint";

import noAllDuplicatedBranches from "./rules/no-all-duplicated-branches.ts";
import noCollapsibleIf from "./rules/no-collapsible-if.ts";
import noElementOverwrite from "./rules/no-element-overwrite.ts";
import noIdenticalExpressions from "./rules/no-identical-expressions.ts";
import noIdenticalFunctions from "./rules/no-identical-functions.ts";
import noRedundantJump from "./rules/no-redundant-jump.ts";
import noUseOfEmptyReturnValue from "./rules/no-use-of-empty-return-value.ts";
import preferImmediateReturn from "./rules/prefer-immediate-return.ts";

const PLUGIN = Object.freeze({
  meta: Object.freeze({ name: "wiki-owned" }),
  rules: Object.freeze({
    "no-all-duplicated-branches": noAllDuplicatedBranches,
    "no-collapsible-if": noCollapsibleIf,
    "no-element-overwrite": noElementOverwrite,
    "no-identical-expressions": noIdenticalExpressions,
    "no-identical-functions": noIdenticalFunctions,
    "no-redundant-jump": noRedundantJump,
    "no-use-of-empty-return-value": noUseOfEmptyReturnValue,
    "prefer-immediate-return": preferImmediateReturn,
  }),
} satisfies TSESLint.FlatConfig.Plugin);

const COMPATIBLE_PLUGIN: CompatiblePlugin = PLUGIN;

export { PLUGIN as rawPlugin };

export default fixupPluginRules(COMPATIBLE_PLUGIN);
