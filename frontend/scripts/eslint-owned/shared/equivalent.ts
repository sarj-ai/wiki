/** Independent structural comparison; never imports the test-body comparator. */
import { AST_NODE_TYPES, TSESLint, type TSESTree } from "@typescript-eslint/utils";

import { children, isNode, readField, unwrap } from "./ast.ts";
import { definedWithin, type Variable, variable } from "./bindings.ts";

const POSITION_FIELDS: ReadonlySet<string> = new Set([
  "parent",
  "loc",
  "range",
  "tokens",
  "comments",
]);

export function equivalent(
  left: readonly TSESTree.Node[] | TSESTree.Node,
  right: readonly TSESTree.Node[] | TSESTree.Node,
  source: TSESLint.SourceCode,
  roots?: readonly [TSESTree.Node, TSESTree.Node],
): boolean {
  const variables = new Map<Variable, Variable>();
  const reverseVariables = new Map<Variable, Variable>();
  type Scope = ReturnType<TSESLint.SourceCode["getScope"]>;
  const scopes = new Map<Scope, Scope>();
  const reverseScopes = new Map<Scope, Scope>();
  const allowRenaming =
    roots !== undefined && !dynamicNames(roots[0], source) && !dynamicNames(roots[1], source);

  if (roots !== undefined && !pairScope(roots[0], roots[1])) return false;
  return compare(left, right);

  function compare(a: unknown, b: unknown): boolean {
    if (a === b) return true;
    if (Array.isArray(a) || Array.isArray(b)) return compareArrays(a, b);
    if (isNode(a) || isNode(b)) return compareNodes(a, b);
    return compareProperties(a, b);
  }

  function compareArrays(a: unknown, b: unknown): boolean {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    const aa: readonly unknown[] = a;
    const bb: readonly unknown[] = b;
    return aa.every((item, i) => compare(item, bb[i]));
  }

  function compareNodes(a: unknown, b: unknown): boolean {
    if (!isNode(a) || !isNode(b)) return false;
    // Cross-scope implementation comparison retains authored type contracts.
    // The pure-operand comparison may erase wrappers for runtime identity.
    const aa = roots === undefined ? unwrap(a) : a;
    const bb = roots === undefined ? unwrap(b) : b;
    if (aa.type !== bb.type || !pairScope(aa, bb)) return false;
    if (
      aa.type === AST_NODE_TYPES.Identifier &&
      bb.type === AST_NODE_TYPES.Identifier &&
      !identifier(aa, bb)
    )
      return false;
    return compareProperties(aa, bb);
  }

  function identifier(a: TSESTree.Identifier, b: TSESTree.Identifier): boolean {
    if (isPropertyName(a) || isPropertyName(b))
      return isPropertyName(a) && isPropertyName(b) && a.name === b.name;
    const va = variable(a, source);
    const vb = variable(b, source);
    if (va === null || vb === null) return va === vb && a.name === b.name;
    if (va === vb) return true;
    if (roots === undefined || !definedWithin(va, roots[0]) || !definedWithin(vb, roots[1]))
      return false;
    if ((!allowRenaming || inferredName(va) || inferredName(vb)) && va.name !== vb.name)
      return false;
    if (
      scopes.get(va.scope) !== vb.scope ||
      va.defs.length !== vb.defs.length ||
      va.defs.some((definition, i) => definition.type !== vb.defs[i]?.type)
    )
      return false;
    if (
      (variables.has(va) && variables.get(va) !== vb) ||
      (reverseVariables.has(vb) && reverseVariables.get(vb) !== va)
    )
      return false;
    variables.set(va, vb);
    reverseVariables.set(vb, va);
    return true;
  }

  function compareProperties(a: unknown, b: unknown): boolean {
    if (a === null || b === null || typeof a !== "object" || typeof b !== "object")
      return Object.is(a, b);
    const keysA = Object.keys(a)
      .filter((key) => !omitField(key, a))
      .toSorted();
    const keysB = Object.keys(b)
      .filter((key) => !omitField(key, b))
      .toSorted();
    return (
      keysA.length === keysB.length &&
      keysA.every((key, i) => key === keysB[i] && compare(readField(a, key), readField(b, key)))
    );
  }

  function pairScope(a: TSESTree.Node, b: TSESTree.Node): boolean {
    const sa = source.getScope(a);
    const sb = source.getScope(b);
    if (sa.block !== a || sb.block !== b) return sa.block !== a && sb.block !== b;
    if (sa.type !== sb.type) return false;
    if (
      (scopes.has(sa) && scopes.get(sa) !== sb) ||
      (reverseScopes.has(sb) && reverseScopes.get(sb) !== sa)
    )
      return false;
    scopes.set(sa, sb);
    reverseScopes.set(sb, sa);
    return true;
  }
}

function dynamicNames(root: TSESTree.Node, source: TSESLint.SourceCode): boolean {
  const stack = [root];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node === undefined) break;
    const callee = node.type === AST_NODE_TYPES.CallExpression ? unwrap(node.callee) : null;
    if (
      node.type === AST_NODE_TYPES.WithStatement ||
      (callee?.type === AST_NODE_TYPES.Identifier && callee.name === "eval")
    )
      return true;
    stack.push(...children(node, source));
  }
  return false;
}

function isPropertyName(node: TSESTree.Identifier): boolean {
  const parent = node.parent;
  return (
    ((parent.type === AST_NODE_TYPES.MemberExpression ||
      parent.type === AST_NODE_TYPES.Property ||
      parent.type === AST_NODE_TYPES.MethodDefinition ||
      parent.type === AST_NODE_TYPES.PropertyDefinition) &&
      !parent.computed &&
      (parent.type === AST_NODE_TYPES.MemberExpression
        ? parent.property === node
        : parent.key === node)) ||
    parent.type === AST_NODE_TYPES.LabeledStatement ||
    parent.type === AST_NODE_TYPES.BreakStatement ||
    parent.type === AST_NODE_TYPES.ContinueStatement
  );
}

function inferredName(binding: Variable): boolean {
  return (
    binding.defs.some(
      (definition) =>
        definition.type === TSESLint.Scope.DefinitionType.FunctionName ||
        definition.type === TSESLint.Scope.DefinitionType.ClassName ||
        (definition.node.type === AST_NODE_TYPES.VariableDeclarator &&
          anonymousName(definition.node.init)) ||
        (definition.name.type === AST_NODE_TYPES.Identifier && defaultName(definition.name)),
    ) ||
    binding.references.some(
      (reference) =>
        defaultName(reference.identifier) ||
        (reference.isWrite() && anonymousName(reference.writeExpr)),
    )
  );
}

function defaultName(node: TSESTree.Node): boolean {
  const parent = node.parent;
  return (
    parent?.type === AST_NODE_TYPES.AssignmentPattern &&
    parent.left === node &&
    anonymousName(parent.right)
  );
}

function anonymousName(node: null | TSESTree.Node | undefined): boolean {
  if (node === null || node === undefined) return false;
  const value = unwrap(node);
  return (
    value.type === AST_NODE_TYPES.ArrowFunctionExpression ||
    ((value.type === AST_NODE_TYPES.FunctionExpression ||
      value.type === AST_NODE_TYPES.ClassExpression) &&
      value.id === null)
  );
}

function omitField(key: string, value: unknown): boolean {
  return (
    POSITION_FIELDS.has(key) ||
    (isNode(value) &&
      ((value.type === AST_NODE_TYPES.Literal && key === "raw") ||
        (value.type === AST_NODE_TYPES.Identifier && key === "name")))
  );
}
