import type { ESTree, Scope, SourceCode } from "@oxlint/plugins";

export function propertyName(
  node: ESTree.MemberExpression,
  source: SourceCode,
): string | null {
  if (!node.computed)
    return node.property.type === "Identifier" ? node.property.name : null;
  return constant(node.property, source, new Set());
}

function constant(
  node: ESTree.Node,
  source: SourceCode,
  seen: Set<ESTree.Node>,
): string | null {
  if (seen.has(node)) return null;
  seen.add(node);
  if (node.type === "Literal") return String(node.value);
  if (node.type === "TemplateLiteral" && node.expressions.length === 0)
    return node.quasis[0]?.value.cooked ?? null;
  if (node.type === "Identifier") {
    let current: Scope | null = source.getScope(node);
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
          ? constant(definition.node.init, source, seen)
          : null;
      }
      current = current.upper;
    }
  }
  return null;
}
