import type { ESTree as TSESTree } from "@oxlint/plugins";

import { isFunction } from "./ast.ts";

export function isLoop(node: TSESTree.Node): boolean {
  return (
    node.type === "ForStatement" ||
    node.type === "ForInStatement" ||
    node.type === "ForOfStatement" ||
    node.type === "WhileStatement" ||
    node.type === "DoWhileStatement"
  );
}

export function jumpDestination(
  node: TSESTree.BreakStatement | TSESTree.ContinueStatement,
): null | TSESTree.Node {
  let current: TSESTree.Node = node.parent;
  while (!isFunction(current)) {
    if (node.label !== null) {
      if (
        current.type === "LabeledStatement" &&
        current.label.name === node.label.name
      ) {
        return node.type === "ContinueStatement" ? current.body : current;
      }
    } else if (
      isLoop(current) ||
      (node.type === "BreakStatement" && current.type === "SwitchStatement")
    ) {
      return current;
    }
    if (current.type === "Program") break;
    current = current.parent;
  }
  return null;
}

export function insideSwitch(node: TSESTree.Node): boolean {
  let current = node.parent;
  while (current != null && !isFunction(current)) {
    if (current.type === "SwitchStatement") return true;
    if (current.type === "Program") return false;
    current = current.parent;
  }
  return false;
}

export function tailReaches(
  node: TSESTree.Node,
  destination: TSESTree.Node,
): boolean {
  let current = node;
  while (current !== destination) {
    const parent = current.parent;
    if (parent == null || !followsOrdinaryTail(parent, current, destination))
      return false;
    current = parent;
  }
  return true;
}

function followsOrdinaryTail(
  parent: TSESTree.Node,
  current: TSESTree.Node,
  destination: TSESTree.Node,
): boolean {
  if (parent.type === "BlockStatement") return parent.body.at(-1) === current;
  if (parent.type === "IfStatement")
    return parent.consequent === current || parent.alternate === current;
  if (parent.type === "CatchClause") return parent.body === current;
  if (parent.type === "TryStatement")
    return (
      parent.finalizer !== current &&
      (parent.block === current || parent.handler === current)
    );
  if (parent.type === "LabeledStatement") return parent.body === current;
  if (parent !== destination) return false;
  if (isLoop(parent)) return "body" in parent && parent.body === current;
  return isFunction(parent) && parent.body === current;
}
