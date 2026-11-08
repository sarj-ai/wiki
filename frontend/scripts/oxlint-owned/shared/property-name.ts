import type { ESTree, Scope } from "@oxlint/plugins";

export function propertyName(
  node: ESTree.MemberExpression,
  scope: Scope,
): string | null {
  if (!node.computed)
    return node.property.type === "Identifier" ? node.property.name : null;
  return constant(node.property, scope, new Set());
}

function constant(
  node: ESTree.Node,
  scope: Scope,
  seen: Set<ESTree.Node>,
): string | null {
  if (seen.has(node)) return null;
  seen.add(node);
  if (node.type === "Literal") return String(node.value);
  if (node.type === "TemplateLiteral" && node.expressions.length === 0)
    return node.quasis[0]?.value.cooked ?? null;
  if (node.type === "Identifier") {
    let current: Scope | null = scope;
    while (current !== null) {
      const binding = current.set.get(node.name);
      if (binding !== undefined) {
        if (
          binding.defs.length !== 1 ||
          binding.references.some(
            (reference) => reference.isWrite() && !reference.init,
          )
        )
          return null;
        const definition = binding.defs[0];
        return definition?.node.type === "VariableDeclarator" &&
          definition.node.init !== null
          ? constant(definition.node.init, scope, seen)
          : null;
      }
      current = current.upper;
    }
  }
  return null;
}
