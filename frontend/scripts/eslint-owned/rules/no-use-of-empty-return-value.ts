import type { ESTree as TSESTree } from "@oxlint/plugins";
import ts from "typescript";

import {
  type FunctionNode,
  isFunction,
  unwrap,
  walkOwnFunction,
} from "../shared/ast.ts";
import {
  hasDynamicEnvironment,
  noLaterVariableWrites,
  variable,
} from "../shared/bindings.ts";
import { createRule } from "../shared/create-rule.ts";
import { typedServices } from "../shared/types.ts";

export default createRule({
  name: "no-use-of-empty-return-value",
  meta: {
    defaultOptions: [],
    type: "problem",
    docs: {
      description:
        "Do not consume a proven empty synchronous result or an awaited void result.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: {
      empty:
        "This expression has no useful return value. Call it for its effects instead.",
    },
  },
  create(context) {
    const source = context.sourceCode;
    if (hasDynamicEnvironment(source)) return {};
    const services = typedServices(context);
    const checker = services?.program.getTypeChecker();

    function check(
      node: TSESTree.AwaitExpression | TSESTree.CallExpression,
    ): void {
      if (!consumed(node)) return;
      let empty = false;
      if (services !== null && checker !== undefined) {
        const type = checker.getTypeAtLocation(
          services.esTreeNodeToTSNodeMap.get(node),
        );
        empty = type.flags === ts.TypeFlags.Void;
      } else if (node.type === "CallExpression") empty = emptyLocal(node);
      if (!empty) return;
      let outer: TSESTree.Node = node;
      while (
        outer.parent.type === "TSAsExpression" ||
        outer.parent.type === "TSSatisfiesExpression" ||
        outer.parent.type === "TSNonNullExpression" ||
        outer.parent.type === "TSTypeAssertion" ||
        outer.parent.type === "ChainExpression"
      )
        outer = outer.parent;
      if (
        outer.parent.type === "AwaitExpression" &&
        services !== null &&
        checker?.getTypeAtLocation(
          services.esTreeNodeToTSNodeMap.get(outer.parent),
        ).flags === ts.TypeFlags.Void
      )
        return;
      context.report({ node, messageId: "empty" });
    }
    function emptyLocal(node: TSESTree.CallExpression): boolean {
      const fn = localFunction(node.callee);
      if (
        fn === null ||
        fn.async ||
        (fn.type !== "ArrowFunctionExpression" && fn.generator) ||
        fn.body.type !== "BlockStatement"
      )
        return false;
      let empty = true;
      walkOwnFunction(fn.body, source, (child) => {
        if (
          (child.type === "ReturnStatement" && child.argument !== null) ||
          child.type === "ThrowStatement" ||
          child.type === "WhileStatement" ||
          child.type === "ForStatement" ||
          child.type === "DoWhileStatement"
        )
          empty = false;
      });
      return empty;
    }

    function localFunction(
      node: TSESTree.Node,
      seen = new Set<TSESTree.Node>(),
    ): FunctionNode | null {
      const value = unwrap(node);
      if (seen.has(value)) return null;
      seen.add(value);
      if (
        value.type === "FunctionExpression" ||
        value.type === "ArrowFunctionExpression"
      )
        return isFunction(value) ? value : null;
      if (value.type !== "Identifier") return null;
      const binding = variable(value, source);
      if (binding?.defs.length !== 1 || !noLaterVariableWrites(binding))
        return null;
      const definition = binding.defs[0]?.node;
      if (definition !== undefined && isFunction(definition)) return definition;
      return definition?.type === "VariableDeclarator" &&
        definition.init !== null
        ? localFunction(definition.init, seen)
        : null;
    }

    return { "CallExpression:exit": check, "AwaitExpression:exit": check };
  },
});

function consumed(node: TSESTree.Node): boolean {
  let current = node;
  for (;;) {
    const parent = current.parent;
    if (parent == null) return false;
    const immediate = directConsumption(parent, current);
    if (immediate !== null) return immediate;
    current = parent;
  }
}

function directConsumption(
  parent: TSESTree.Node,
  current: TSESTree.Node,
): boolean | null {
  if (
    parent.type === "ExpressionStatement" ||
    parent.type === "ReturnStatement"
  )
    return false;
  if (parent.type === "ArrowFunctionExpression") return parent.body !== current;
  if (parent.type === "UnaryExpression") return parent.operator !== "void";
  if (parent.type === "ForStatement")
    return parent.init !== current && parent.update !== current;
  if (parent.type === "SequenceExpression")
    return parent.expressions.at(-1) === current ? null : false;
  if (parent.type === "LogicalExpression")
    return parent.left === current ? true : null;
  if (parent.type === "ConditionalExpression")
    return parent.test === current ? true : null;
  return carriesValue(parent) ? null : true;
}

function carriesValue(node: TSESTree.Node): boolean {
  return (
    node.type === "AwaitExpression" ||
    node.type === "TSAsExpression" ||
    node.type === "TSSatisfiesExpression" ||
    node.type === "TSNonNullExpression" ||
    node.type === "TSTypeAssertion" ||
    node.type === "ChainExpression"
  );
}
