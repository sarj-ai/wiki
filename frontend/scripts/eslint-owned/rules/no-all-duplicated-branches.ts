import { AST_NODE_TYPES, type TSESTree } from "@typescript-eslint/utils";

import { statements } from "../shared/ast.ts";
import { jumpDestination } from "../shared/completion.ts";
import { createRule } from "../shared/create-rule.ts";
import { equivalent } from "../shared/equivalent.ts";

export default createRule({
  name: "no-all-duplicated-branches",
  meta: {
    defaultOptions: [],
    type: "suggestion",
    docs: {
      description: "Report complete alternatives with the same implementation.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: { duplicated: "Every alternative has the same implementation." },
  },
  create(context) {
    const source = context.sourceCode;
    return {
      "IfStatement:exit"(node) {
        if (node.parent.type === AST_NODE_TYPES.IfStatement && node.parent.alternate === node)
          return;
        const branches: TSESTree.Statement[] = [];
        let current: TSESTree.IfStatement = node;
        for (;;) {
          branches.push(current.consequent);
          if (current.alternate === null) return;
          if (current.alternate.type === AST_NODE_TYPES.IfStatement) current = current.alternate;
          else {
            branches.push(current.alternate);
            break;
          }
        }
        const first = branches[0];
        if (
          first !== undefined &&
          branches
            .slice(1)
            .every((branch) =>
              equivalent(statements(first), statements(branch), source, [first, branch]),
            )
        ) {
          context.report({ node: node.test, messageId: "duplicated" });
        }
      },
      "ConditionalExpression:exit"(node) {
        if (equivalent(node.consequent, node.alternate, source))
          context.report({ node: node.test, messageId: "duplicated" });
      },
      "SwitchStatement:exit"(node) {
        const complete = node.cases.some((branch) => branch.test === null);
        // Empty labels may share the next implementation; an empty final case
        // is a genuine empty alternative and must never disappear from the proof.
        if (!complete || node.cases.at(-1)?.consequent.length === 0) return;
        const branches = node.cases.filter((branch) => branch.consequent.length > 0);
        if (
          branches.length < 2 ||
          branches.slice(0, -1).some((branch) => {
            const last = branch.consequent.at(-1);
            return (
              last?.type !== AST_NODE_TYPES.ReturnStatement &&
              last?.type !== AST_NODE_TYPES.ThrowStatement &&
              !(last?.type === AST_NODE_TYPES.BreakStatement && jumpDestination(last) === node)
            );
          })
        )
          return;
        const bodies = branches.map((branch) => {
          const body = branch.consequent;
          const last = body.at(-1);
          return last?.type === AST_NODE_TYPES.BreakStatement &&
            last.label === null &&
            jumpDestination(last) === node
            ? body.slice(0, -1)
            : body;
        });
        const first = bodies[0];
        const firstBranch = branches[0];
        if (first === undefined || firstBranch === undefined || first.length === 0) return;
        if (
          bodies.slice(1).every((body, i) => {
            const branch = branches[i + 1];
            return branch !== undefined && equivalent(first, body, source, [firstBranch, branch]);
          })
        )
          context.report({ node: node.discriminant, messageId: "duplicated" });
      },
    };
  },
});
