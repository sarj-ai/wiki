import { AST_NODE_TYPES, type TSESTree } from "@typescript-eslint/utils";

import { isFunction } from "../shared/ast.ts";
import { insideSwitch, jumpDestination, tailReaches } from "../shared/completion.ts";
import { createRule } from "../shared/create-rule.ts";

export default createRule({
  name: "no-redundant-jump",
  meta: {
    defaultOptions: [],
    type: "suggestion",
    docs: {
      description: "Report jumps with the same destination as ordinary completion.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: { redundant: "This jump reaches the same destination as ordinary completion." },
  },
  create(context) {
    return {
      [AST_NODE_TYPES.ReturnStatement](node) {
        if (node.argument !== null || exempt(node) || insideSwitch(node)) return;
        let destination = node.parent;
        while (!isFunction(destination)) {
          if (destination.type === AST_NODE_TYPES.Program) return;
          destination = destination.parent;
        }
        if (tailReaches(node, destination)) context.report({ node, messageId: "redundant" });
      },
      [AST_NODE_TYPES.ContinueStatement](node) {
        if (node.label !== null || exempt(node)) return;
        const destination = jumpDestination(node);
        if (destination !== null && tailReaches(node, destination))
          context.report({ node, messageId: "redundant" });
      },
      [AST_NODE_TYPES.BreakStatement](node) {
        if (exempt(node) || insideSwitch(node)) return;
        const destination = jumpDestination(node);
        if (
          destination?.type === AST_NODE_TYPES.LabeledStatement &&
          tailReaches(node, destination)
        ) {
          context.report({ node, messageId: "redundant" });
        }
      },
    };
  },
});

function exempt(node: TSESTree.Node): boolean {
  return node.parent?.type === AST_NODE_TYPES.BlockStatement && node.parent.body.length === 1;
}
