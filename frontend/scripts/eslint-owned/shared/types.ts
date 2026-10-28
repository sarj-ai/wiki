import { ESLintUtils, type TSESLint, type TSESTree } from "@typescript-eslint/utils";
import ts from "typescript";

export function typedServices<M extends string, O extends readonly unknown[]>(
  context: TSESLint.RuleContext<M, O>,
) {
  return context.sourceCode.parserServices?.program ? ESLintUtils.getParserServices(context) : null;
}

export type TypedServices = NonNullable<ReturnType<typeof typedServices>>;

export function primitiveType(type: ts.Type): boolean {
  if (type.isUnion()) return type.types.every(primitiveType);
  return (
    (type.flags &
      (ts.TypeFlags.StringLike |
        ts.TypeFlags.NumberLike |
        ts.TypeFlags.BigIntLike |
        ts.TypeFlags.BooleanLike |
        ts.TypeFlags.Null |
        ts.TypeFlags.Undefined)) !==
    0
  );
}

export function builtinConstructor(
  node: TSESTree.Identifier,
  name: "Array" | "Map" | "Object" | "Set",
  services: TypedServices,
): boolean {
  const checker = services.program.getTypeChecker();
  const symbol = checker.getSymbolAtLocation(services.esTreeNodeToTSNodeMap.get(node));
  const declarations = symbol?.getDeclarations();
  return (
    symbol?.getName() === name &&
    declarations !== undefined &&
    declarations.length > 0 &&
    declarations.every((declaration) =>
      services.program.isSourceFileDefaultLibrary(declaration.getSourceFile()),
    )
  );
}
