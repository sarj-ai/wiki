import { AST_NODE_TYPES, type TSESTree } from "@typescript-eslint/utils";
import ts from "typescript";

import { type FunctionNode, unwrap, walkOwnFunction } from "../shared/ast.ts";
import { hasDynamicEnvironment, noLaterVariableWrites, variable } from "../shared/bindings.ts";
import { createRule } from "../shared/create-rule.ts";
import { typedServices } from "../shared/types.ts";

export default createRule({
  name: "no-use-of-empty-return-value",
  meta: {
    defaultOptions: [],
    type: "problem",
    docs: {
      description: "Do not consume a proven empty synchronous result or an awaited void result.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: {
      empty: "This expression has no useful return value. Call it for its effects instead.",
    },
  },
  create(context) {
    const source = context.sourceCode;
    if (hasDynamicEnvironment(source)) return {};
    const services = typedServices(context);
    const checker = services?.program.getTypeChecker();

    function check(node: TSESTree.AwaitExpression | TSESTree.CallExpression): void {
      if (!consumed(node)) return;
      let empty = false;
      if (services !== null && checker !== undefined) {
        const type = checker.getTypeAtLocation(services.esTreeNodeToTSNodeMap.get(node));
        empty = type.flags === ts.TypeFlags.Void;
      } else if (node.type === AST_NODE_TYPES.CallExpression) empty = emptyLocal(node);
      if (!empty) return;
      let outer: TSESTree.Node = node;
      while (
        outer.parent.type === AST_NODE_TYPES.TSAsExpression ||
        outer.parent.type === AST_NODE_TYPES.TSSatisfiesExpression ||
        outer.parent.type === AST_NODE_TYPES.TSNonNullExpression ||
        outer.parent.type === AST_NODE_TYPES.TSTypeAssertion ||
        outer.parent.type === AST_NODE_TYPES.ChainExpression
      )
        outer = outer.parent;
      if (
        outer.parent.type === AST_NODE_TYPES.AwaitExpression &&
        services !== null &&
        checker?.getTypeAtLocation(services.esTreeNodeToTSNodeMap.get(outer.parent)).flags ===
          ts.TypeFlags.Void
      )
        return;
      context.report({ node, messageId: "empty" });
    }
    function emptyLocal(node: TSESTree.CallExpression): boolean {
      const fn = localFunction(node.callee);
      if (
        fn === null ||
        fn.async ||
        (fn.type !== AST_NODE_TYPES.ArrowFunctionExpression && fn.generator) ||
        fn.body.type !== AST_NODE_TYPES.BlockStatement
      )
        return false;
      let empty = true;
      walkOwnFunction(fn.body, source, (child) => {
        if (
          (child.type === AST_NODE_TYPES.ReturnStatement && child.argument !== null) ||
          child.type === AST_NODE_TYPES.ThrowStatement ||
          child.type === AST_NODE_TYPES.WhileStatement ||
          child.type === AST_NODE_TYPES.ForStatement ||
          child.type === AST_NODE_TYPES.DoWhileStatement
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
        value.type === AST_NODE_TYPES.FunctionExpression ||
        value.type === AST_NODE_TYPES.ArrowFunctionExpression
      )
        return value;
      if (value.type !== AST_NODE_TYPES.Identifier) return null;
      const binding = variable(value, source);
      if (binding?.defs.length !== 1 || !noLaterVariableWrites(binding)) return null;
      const definition = binding.defs[0]?.node;
      if (definition?.type === AST_NODE_TYPES.FunctionDeclaration) return definition;
      return definition?.type === AST_NODE_TYPES.VariableDeclarator && definition.init !== null
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
    if (parent === undefined) return false;
    const immediate = directConsumption(parent, current);
    if (immediate !== null) return immediate;
    current = parent;
  }
}

function directConsumption(parent: TSESTree.Node, current: TSESTree.Node): boolean | null {
  if (
    parent.type === AST_NODE_TYPES.ExpressionStatement ||
    parent.type === AST_NODE_TYPES.ReturnStatement
  )
    return false;
  if (parent.type === AST_NODE_TYPES.ArrowFunctionExpression) return parent.body !== current;
  if (parent.type === AST_NODE_TYPES.UnaryExpression) return parent.operator !== "void";
  if (parent.type === AST_NODE_TYPES.ForStatement)
    return parent.init !== current && parent.update !== current;
  if (parent.type === AST_NODE_TYPES.SequenceExpression)
    return parent.expressions.at(-1) === current ? null : false;
  if (parent.type === AST_NODE_TYPES.LogicalExpression)
    return parent.left === current ? true : null;
  if (parent.type === AST_NODE_TYPES.ConditionalExpression)
    return parent.test === current ? true : null;
  return carriesValue(parent) ? null : true;
}

function carriesValue(node: TSESTree.Node): boolean {
  return (
    node.type === AST_NODE_TYPES.AwaitExpression ||
    node.type === AST_NODE_TYPES.TSAsExpression ||
    node.type === AST_NODE_TYPES.TSSatisfiesExpression ||
    node.type === AST_NODE_TYPES.TSNonNullExpression ||
    node.type === AST_NODE_TYPES.TSTypeAssertion ||
    node.type === AST_NODE_TYPES.ChainExpression
  );
}
