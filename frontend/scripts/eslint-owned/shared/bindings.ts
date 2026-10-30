import { AST_NODE_TYPES, ASTUtils, TSESLint, type TSESTree } from "@typescript-eslint/utils";

import { children, contains, isFunction, unwrap } from "./ast.ts";

const DYNAMIC_ENVIRONMENTS = new WeakMap<TSESLint.SourceCode, boolean>();

// Direct eval and with can read/write bindings without static references.
export function hasDynamicEnvironment(source: TSESLint.SourceCode): boolean {
  const cached = DYNAMIC_ENVIRONMENTS.get(source);
  if (cached !== undefined) return cached;
  const stack: TSESTree.Node[] = [source.ast];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node === undefined) break;
    const callee = node.type === AST_NODE_TYPES.CallExpression ? unwrap(node.callee) : null;
    if (
      node.type === AST_NODE_TYPES.WithStatement ||
      (callee?.type === AST_NODE_TYPES.Identifier && callee.name === "eval")
    ) {
      DYNAMIC_ENVIRONMENTS.set(source, true);
      return true;
    }
    stack.push(...children(node, source));
  }
  DYNAMIC_ENVIRONMENTS.set(source, false);
  return false;
}

export type Variable = TSESLint.Scope.Variable;

// A type-only ambient or foreign binding can be backed by a runtime getter.
export function lexicalValueBinding(binding: Variable): boolean {
  if (binding.defs.length !== 1) return false;
  const definition = binding.defs[0];
  if (
    definition?.type === TSESLint.Scope.DefinitionType.Parameter ||
    definition?.type === TSESLint.Scope.DefinitionType.CatchClause
  )
    return true;
  if (definition?.type !== TSESLint.Scope.DefinitionType.Variable) return false;
  const declaration = definition.parent;
  return (
    !declaration.declare &&
    (declaration.kind !== "var" || binding.scope.type !== TSESLint.Scope.ScopeType.global)
  );
}

// Source order alone cannot initialize a captured binding before a hoisted call.
export function initializedLexicalRead(node: TSESTree.Identifier, binding: Variable): boolean {
  if (!lexicalValueBinding(binding) || !noLaterVariableWrites(binding)) return false;
  const definition = binding.defs[0];
  if (definition === undefined) return false;
  if (definition.type === TSESLint.Scope.DefinitionType.Parameter) {
    return (
      isFunction(definition.node) &&
      contains(definition.node.body, node) &&
      executionOwner(node) === definition.node
    );
  }
  if (definition.type === TSESLint.Scope.DefinitionType.CatchClause) {
    return contains(definition.node.body, node);
  }
  if (
    definition.type !== TSESLint.Scope.DefinitionType.Variable ||
    definition.node.range[1] >= node.range[0] ||
    executionOwner(definition.node) !== executionOwner(node)
  )
    return false;
  let container = definition.parent.parent;
  if (container.type === AST_NODE_TYPES.ExportNamedDeclaration) container = container.parent;
  // Source order within a function is insufficient across switch cases or
  // conditional declarations. A preceding declaration in an enclosing ordinary
  // statement list must execute before this same-owner read can be reached.
  return (
    (container.type === AST_NODE_TYPES.Program ||
      container.type === AST_NODE_TYPES.BlockStatement) &&
    contains(container, node)
  );
}

export function variable(node: TSESTree.Identifier, source: TSESLint.SourceCode): null | Variable {
  return ASTUtils.findVariable(source.getScope(node), node.name) ?? null;
}

export function definedWithin(binding: Variable, root: TSESTree.Node): boolean {
  return (
    binding.defs.length > 0 && binding.defs.every((definition) => contains(root, definition.node))
  );
}

export function noLaterVariableWrites(binding: Variable): boolean {
  let initializer: TSESTree.Node | undefined;
  return binding.references.every((reference) => {
    if (!reference.isWrite()) return true;
    if (reference.init !== true) return false;
    if (initializer !== undefined && initializer !== reference.identifier) return false;
    initializer = reference.identifier;
    return true;
  });
}

export function onlyValueReads(binding: Variable, allowed: ReadonlySet<TSESTree.Node>): boolean {
  return binding.references.every(
    (reference) =>
      reference.init === true || (reference.isReadOnly() && allowed.has(reference.identifier)),
  );
}

function executionOwner(node: TSESTree.Node): TSESTree.Node {
  let current = node;
  while (current.type !== AST_NODE_TYPES.Program && !isFunction(current)) current = current.parent;
  return current;
}
