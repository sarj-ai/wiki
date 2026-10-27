import { AST_NODE_TYPES, type TSESTree } from "@typescript-eslint/utils";

import { isFunction } from "./ast.ts";

export function isLoop(node: TSESTree.Node): boolean {
  return (
    node.type === AST_NODE_TYPES.ForStatement ||
    node.type === AST_NODE_TYPES.ForInStatement ||
    node.type === AST_NODE_TYPES.ForOfStatement ||
    node.type === AST_NODE_TYPES.WhileStatement ||
    node.type === AST_NODE_TYPES.DoWhileStatement
  );
}

export function jumpDestination(
  node: TSESTree.BreakStatement | TSESTree.ContinueStatement,
): null | TSESTree.Node {
  let current: TSESTree.Node = node.parent;
  while (!isFunction(current)) {
    if (node.label !== null) {
      if (
        current.type === AST_NODE_TYPES.LabeledStatement &&
        current.label.name === node.label.name
      ) {
        return node.type === AST_NODE_TYPES.ContinueStatement ? current.body : current;
      }
    } else if (
      isLoop(current) ||
      (node.type === AST_NODE_TYPES.BreakStatement &&
        current.type === AST_NODE_TYPES.SwitchStatement)
    ) {
      return current;
    }
    if (current.type === AST_NODE_TYPES.Program) break;
    current = current.parent;
  }
  return null;
}

export function insideSwitch(node: TSESTree.Node): boolean {
  let current = node.parent;
  while (current !== undefined && !isFunction(current)) {
    if (current.type === AST_NODE_TYPES.SwitchStatement) return true;
    if (current.type === AST_NODE_TYPES.Program) return false;
    current = current.parent;
  }
  return false;
}

// Follow only proven ordinary tail completion, never a loop iteration or an overriding finalizer.
export function tailReaches(node: TSESTree.Node, destination: TSESTree.Node): boolean {
  let current = node;
  while (current !== destination) {
    const parent = current.parent;
    if (parent === undefined || !followsOrdinaryTail(parent, current, destination)) return false;
    current = parent;
  }
  return true;
}

function followsOrdinaryTail(
  parent: TSESTree.Node,
  current: TSESTree.Node,
  destination: TSESTree.Node,
): boolean {
  if (parent.type === AST_NODE_TYPES.BlockStatement) return parent.body.at(-1) === current;
  if (parent.type === AST_NODE_TYPES.IfStatement)
    return parent.consequent === current || parent.alternate === current;
  if (parent.type === AST_NODE_TYPES.CatchClause) return parent.body === current;
  if (parent.type === AST_NODE_TYPES.TryStatement)
    return parent.finalizer !== current && (parent.block === current || parent.handler === current);
  if (parent.type === AST_NODE_TYPES.LabeledStatement) return parent.body === current;
  if (parent !== destination) return false;
  if (isLoop(parent)) return "body" in parent && parent.body === current;
  return isFunction(parent) && parent.body === current;
}
