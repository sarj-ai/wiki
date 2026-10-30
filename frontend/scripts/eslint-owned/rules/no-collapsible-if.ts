import { AST_NODE_TYPES } from "@typescript-eslint/utils";

import { createRule } from "../shared/create-rule.ts";

export default createRule({
  name: "no-collapsible-if",
  meta: {
    defaultOptions: [],
    type: "suggestion",
    docs: {
      description: "Report a single nested condition with no alternative or intervening work.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: {
      combine: "Combine these nested conditions while preserving their short-circuit order.",
    },
  },
  create(context) {
    return {
      [AST_NODE_TYPES.IfStatement](node) {
        if (node.alternate !== null) return;
        const inner =
          node.consequent.type === AST_NODE_TYPES.BlockStatement
            ? node.consequent.body.length === 1
              ? node.consequent.body[0]
              : undefined
            : node.consequent;
        if (inner?.type === AST_NODE_TYPES.IfStatement && inner.alternate === null) {
          context.report({ node: inner.test, messageId: "combine" });
        }
      },
    };
  },
});
