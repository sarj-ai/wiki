import type { ESTree as TSESTree, SourceCode, Variable } from "@oxlint/plugins";

import type { Identifier } from "./ast.ts";
import { children, contains, isFunction, unwrap } from "./ast.ts";

// Direct eval and with can read/write bindings without static references.
export function hasDynamicEnvironment(source: SourceCode): boolean {
  const stack: TSESTree.Node[] = [source.ast];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node === undefined) break;
    const callee = node.type === "CallExpression" ? unwrap(node.callee) : null;
    if (
      node.type === "WithStatement" ||
      (callee?.type === "Identifier" && callee.name === "eval")
    ) {
      return true;
    }
    stack.push(...children(node, source));
  }
  return false;
}

export type { Variable } from "@oxlint/plugins";

// A type-only ambient or foreign binding can be backed by a runtime getter.
export function lexicalValueBinding(binding: Variable): boolean {
  if (binding.defs.length !== 1) return false;
  const definition = binding.defs[0];
  if (definition?.type === "Parameter" || definition?.type === "CatchClause")
    return true;
  if (definition?.type !== "Variable") return false;
  const declaration = definition.parent;
  return (
    declaration?.type === "VariableDeclaration" &&
    !declaration.declare &&
    (declaration.kind !== "var" || binding.scope.type !== "global")
  );
}

// Source order alone cannot initialize a captured binding before a hoisted call.
export function initializedLexicalRead(
  node: Identifier,
  binding: Variable,
): boolean {
  if (!lexicalValueBinding(binding) || !noLaterVariableWrites(binding))
    return false;
  const definition = binding.defs[0];
  if (definition === undefined) return false;
  if (definition.type === "Parameter") {
    return (
      isFunction(definition.node) &&
      contains(definition.node.body, node) &&
      executionOwner(node) === definition.node
    );
  }
  if (definition.type === "CatchClause") {
    return (
      definition.node.type === "CatchClause" &&
      contains(definition.node.body, node)
    );
  }
  if (
    definition.type !== "Variable" ||
    definition.node.range[1] >= node.range[0] ||
    executionOwner(definition.node) !== executionOwner(node)
  )
    return false;
  let container = definition.parent?.parent;
  if (container?.type === "ExportNamedDeclaration")
    container = container.parent;
  // Source order within a function is insufficient across switch cases or
  // conditional declarations. A preceding declaration in an enclosing ordinary
  // statement list must execute before this same-owner read can be reached.
  return (
    (container?.type === "Program" || container?.type === "BlockStatement") &&
    contains(container, node)
  );
}

export function variable(
  node: Identifier,
  source: SourceCode,
): null | Variable {
  let scope: ReturnType<SourceCode["getScope"]> | null = source.getScope(node);
  while (scope !== null) {
    const binding = scope.set.get(node.name);
    if (binding !== undefined) return binding;
    scope = scope.upper;
  }
  return null;
}

export function definedWithin(binding: Variable, root: TSESTree.Node): boolean {
  return (
    binding.defs.length > 0 &&
    binding.defs.every((definition) => contains(root, definition.node))
  );
}

export function noLaterVariableWrites(binding: Variable): boolean {
  let initializer: TSESTree.Node | undefined;
  return binding.references.every((reference) => {
    if (!reference.isWrite()) return true;
    if (!reference.init) return false;
    if (initializer !== undefined && initializer !== reference.identifier)
      return false;
    initializer = reference.identifier;
    return true;
  });
}

export function onlyValueReads(
  binding: Variable,
  allowed: ReadonlySet<TSESTree.Node>,
): boolean {
  return binding.references.every(
    (reference) =>
      reference.init ||
      (reference.isReadOnly() && allowed.has(reference.identifier)),
  );
}

function executionOwner(node: TSESTree.Node): TSESTree.Node {
  let current = node;
  while (current.type !== "Program" && !isFunction(current)) {
    current = current.parent;
  }
  return current;
}
