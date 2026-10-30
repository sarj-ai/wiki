import { AST_NODE_TYPES, type TSESTree } from "@typescript-eslint/utils";

import { unwrap } from "../shared/ast.ts";
import {
  hasDynamicEnvironment,
  initializedLexicalRead,
  noLaterVariableWrites,
  variable,
} from "../shared/bindings.ts";
import { createRule } from "../shared/create-rule.ts";
import { equivalent } from "../shared/equivalent.ts";
import { primitiveType, typedServices } from "../shared/types.ts";

const BINARY: ReadonlySet<string> = new Set([
  "-",
  "/",
  "%",
  "==",
  "===",
  "<",
  "<=",
  ">",
  ">=",
  "&",
  "|",
  "^",
]);

export default createRule({
  name: "no-identical-expressions",
  meta: {
    defaultOptions: [],
    type: "problem",
    docs: {
      description:
        "Report the same stable primitive operand on both sides of a redundant operation.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: {
      identical:
        "Both operands evaluate the same primitive expression; check the intended operation.",
    },
  },
  create(context) {
    const source = context.sourceCode;
    if (hasDynamicEnvironment(source)) return {};
    const services = typedServices(context);
    const checker = services?.program.getTypeChecker();

    function check(node: TSESTree.BinaryExpression | TSESTree.LogicalExpression): void {
      // Self-inequality is a documented NaN idiom. Shifts, addition,
      // multiplication and exponentiation have useful equal-operand meanings.
      if (node.type === AST_NODE_TYPES.BinaryExpression && !BINARY.has(node.operator)) return;
      if (pure(node.left) && pure(node.right) && equivalent(node.left, node.right, source)) {
        context.report({ node, messageId: "identical" });
      }
    }
    function pure(node: TSESTree.Node, seen = new Set<TSESTree.Node>()): boolean {
      const value = unwrap(node);
      if (seen.has(value)) return false;
      seen.add(value);
      if (value.type === AST_NODE_TYPES.Literal) return !("regex" in value);
      if (value.type === AST_NODE_TYPES.Identifier) return pureIdentifier(value, seen);
      if (value.type === AST_NODE_TYPES.UnaryExpression) {
        return (
          ["!", "+", "-", "~", "typeof", "void"].includes(value.operator) &&
          pure(value.argument, seen)
        );
      }
      if (
        value.type === AST_NODE_TYPES.BinaryExpression ||
        value.type === AST_NODE_TYPES.LogicalExpression
      ) {
        return pure(value.left, new Set(seen)) && pure(value.right, new Set(seen));
      }
      return false;
    }

    function pureIdentifier(node: TSESTree.Identifier, seen: Set<TSESTree.Node>): boolean {
      const binding = variable(node, source);
      if (binding === null || !initializedLexicalRead(node, binding)) return false;
      if (services !== null && checker !== undefined) {
        const definition = binding.defs[0]?.node;
        if (
          definition?.type === AST_NODE_TYPES.VariableDeclarator &&
          definition.init !== null &&
          !primitiveType(
            checker.getTypeAtLocation(services.esTreeNodeToTSNodeMap.get(unwrap(definition.init))),
          )
        )
          return false;
        return primitiveType(checker.getTypeAtLocation(services.esTreeNodeToTSNodeMap.get(node)));
      }
      if (!noLaterVariableWrites(binding)) return false;
      const definition = binding.defs[0];
      return (
        definition?.node.type === AST_NODE_TYPES.VariableDeclarator &&
        definition.node.init !== null &&
        pure(definition.node.init, seen)
      );
    }

    return { "BinaryExpression:exit": check, "LogicalExpression:exit": check };
  },
});
