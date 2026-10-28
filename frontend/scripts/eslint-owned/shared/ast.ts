/**
 * Typed AST primitives. The erased-wrapper and checked visitor-key patterns are
 * adapted from the MIT Code Standards b4e7f901 helpers identified in the root
 * README.md#mit-helper-provenance.
 */
import { AST_NODE_TYPES, type TSESLint, type TSESTree } from "@typescript-eslint/utils";

export function unwrap(node: TSESTree.Node): TSESTree.Node {
  let current = node;
  while (
    current.type === AST_NODE_TYPES.TSAsExpression ||
    current.type === AST_NODE_TYPES.TSSatisfiesExpression ||
    current.type === AST_NODE_TYPES.TSNonNullExpression ||
    current.type === AST_NODE_TYPES.TSTypeAssertion
  ) {
    current = current.expression;
  }
  return current;
}

export function isNode(value: unknown): value is TSESTree.Node {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    typeof value.type === "string" &&
    "range" in value &&
    Array.isArray(value.range)
  );
}

export function children(
  node: TSESTree.Node,
  source: TSESLint.SourceCode,
): readonly TSESTree.Node[] {
  const result: TSESTree.Node[] = [];
  const keys = source.visitorKeys[node.type];
  if (keys === undefined) return result;
  for (const key of keys) {
    const value = readField(node, key);
    if (isNode(value)) result.push(value);
    else if (Array.isArray(value)) {
      const items: readonly unknown[] = value;
      for (const item of items) if (isNode(item)) result.push(item);
    }
  }
  return result;
}

export function readField(value: object, key: string): unknown {
  return isRecord(value) ? value[key] : undefined;
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return value !== null && (typeof value === "object" || typeof value === "function");
}

export type FunctionNode =
  TSESTree.ArrowFunctionExpression | TSESTree.FunctionDeclaration | TSESTree.FunctionExpression;

export function isFunction(node: TSESTree.Node): node is FunctionNode {
  return (
    node.type === AST_NODE_TYPES.FunctionDeclaration ||
    node.type === AST_NODE_TYPES.FunctionExpression ||
    node.type === AST_NODE_TYPES.ArrowFunctionExpression
  );
}

export function contains(root: TSESTree.Node, node: TSESTree.Node): boolean {
  return root.range[0] <= node.range[0] && node.range[1] <= root.range[1];
}

export function walkOwnFunction(
  root: TSESTree.Node,
  source: TSESLint.SourceCode,
  visit: (node: TSESTree.Node) => void,
): void {
  const stack = [root];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node === undefined) break;
    visit(node);
    if (node !== root && isFunction(node)) continue;
    stack.push(...children(node, source));
  }
}

export function statements(node: TSESTree.Statement): readonly TSESTree.Statement[] {
  return node.type === AST_NODE_TYPES.BlockStatement ? node.body : [node];
}
