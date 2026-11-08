import type { ESTree as TSESTree } from "@oxlint/plugins";

import { isFunction } from "../shared/ast.ts";
import {
  insideSwitch,
  jumpDestination,
  tailReaches,
} from "../shared/completion.ts";
import { createRule } from "../shared/create-rule.ts";

export default createRule({
  name: "no-redundant-jump",
  meta: {
    defaultOptions: [],
    type: "suggestion",
    docs: {
      description:
        "Report jumps with the same destination as ordinary completion.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: {
      redundant:
        "This jump reaches the same destination as ordinary completion.",
    },
  },
  create(context) {
    return {
      ReturnStatement(node) {
        if (node.argument !== null || exempt(node) || insideSwitch(node))
          return;
        let destination = node.parent;
        while (!isFunction(destination)) {
          if (destination.type === "Program") return;
          destination = destination.parent;
        }
        if (tailReaches(node, destination))
          context.report({ node, messageId: "redundant" });
      },
      ContinueStatement(node) {
        if (node.label !== null || exempt(node)) return;
        const destination = jumpDestination(node);
        if (destination !== null && tailReaches(node, destination))
          context.report({ node, messageId: "redundant" });
      },
      BreakStatement(node) {
        if (exempt(node) || insideSwitch(node)) return;
        const destination = jumpDestination(node);
        if (
          destination?.type === "LabeledStatement" &&
          tailReaches(node, destination)
        ) {
          context.report({ node, messageId: "redundant" });
        }
      },
    };
  },
});

function exempt(node: TSESTree.Node): boolean {
  return (
    node.parent?.type === "BlockStatement" && node.parent.body.length === 1
  );
}
