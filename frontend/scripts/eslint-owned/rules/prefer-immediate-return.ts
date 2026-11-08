import type { ESTree as TSESTree } from "@oxlint/plugins";
import ts from "typescript";

import {
  type FunctionNode,
  isFunction,
  unwrap,
  type Identifier,
} from "../shared/ast.ts";
import {
  hasDynamicEnvironment,
  onlyValueReads,
  variable,
} from "../shared/bindings.ts";
import { createRule } from "../shared/create-rule.ts";
import { requireTypedServices } from "../shared/types.ts";

export default createRule({
  name: "prefer-immediate-return",
  meta: {
    defaultOptions: [],
    type: "suggestion",
    docs: {
      description:
        "Return or throw an initializer directly when its temporary adds no type or lifetime contract.",
      requiresTypeChecking: true,
    },
    schema: [],
    messages: {
      immediate:
        "{{operation}} this initializer directly; its temporary has no other value use.",
    },
  },
  create(context) {
    if (hasDynamicEnvironment(context.sourceCode)) return {};
    const services = requireTypedServices(context);
    const checker = services.program.getTypeChecker();
    const source = context.sourceCode;

    function checkPair(
      declaration: TSESTree.Node | undefined,
      next: TSESTree.Node | undefined,
    ): void {
      if (
        declaration?.type !== "VariableDeclaration" ||
        declaration.kind !== "const" ||
        declaration.declarations.length !== 1 ||
        (next?.type !== "ReturnStatement" && next?.type !== "ThrowStatement")
      )
        return;
      const entry = declaration.declarations[0];
      if (
        entry?.id.type !== "Identifier" ||
        entry.id.typeAnnotation != null ||
        entry.init === null ||
        next.argument?.type !== "Identifier" ||
        protectedInitializer(entry.init)
      )
        return;
      const binding = variable(entry.id, source);
      if (
        binding === null ||
        variable(next.argument, source) !== binding ||
        !onlyValueReads(binding, new Set([next.argument]))
      )
        return;
      const fn = owner(declaration);
      if (
        fn === null ||
        (next.type === "ReturnStatement" && hasReturnContract(fn)) ||
        !sameValueContract(entry.init, entry.id)
      )
        return;
      context.report({
        node: entry.id,
        messageId: "immediate",
        data: {
          operation: next.type === "ReturnStatement" ? "Return" : "Throw",
        },
      });
    }

    function hasReturnContract(fn: FunctionNode): boolean {
      if (fn.returnType != null) return true;
      const native = services.esTreeNodeToTSNodeMap.get(fn);
      if (
        (ts.isArrowFunction(native) || ts.isFunctionExpression(native)) &&
        checker.getContextualType(native) !== undefined
      )
        return true;
      if (fn.type === "FunctionDeclaration" && fn.id !== null) {
        const symbol = checker.getSymbolAtLocation(
          services.esTreeNodeToTSNodeMap.get(fn.id),
        );
        if ((symbol?.getDeclarations()?.length ?? 0) > 1) return true;
      }
      return false;
    }

    function sameValueContract(
      initialNode: TSESTree.Node,
      localNode: Identifier,
    ): boolean {
      const initial = checker.getTypeAtLocation(
        services.esTreeNodeToTSNodeMap.get(initialNode),
      );
      const local = checker.getTypeAtLocation(
        services.esTreeNodeToTSNodeMap.get(localNode),
      );
      // Named evaluation can assign the binding's name to a function/class.
      // Keep all callable/constructable temporaries rather than erase it.
      if (hasCallableShape(initial) || hasCallableShape(local)) return false;
      const unknown =
        ts.TypeFlags.Any |
        ts.TypeFlags.Unknown |
        ts.TypeFlags.Never |
        ts.TypeFlags.TypeParameter;
      return (
        ((initial.flags | local.flags) & unknown) === 0 &&
        checker.isTypeAssignableTo(initial, local) &&
        checker.isTypeAssignableTo(local, initial)
      );
    }

    return {
      "BlockStatement:exit"(block) {
        for (let i = 0; i + 1 < block.body.length; i += 1)
          checkPair(block.body[i], block.body[i + 1]);
      },
    };
  },
});

function protectedInitializer(node: TSESTree.Node): boolean {
  if (
    node.type === "TSAsExpression" ||
    node.type === "TSSatisfiesExpression" ||
    node.type === "TSTypeAssertion" ||
    node.type === "TSNonNullExpression"
  )
    return true;
  const value = unwrap(node);
  return (
    value.type === "ArrowFunctionExpression" ||
    ((value.type === "FunctionExpression" ||
      value.type === "ClassExpression") &&
      value.id === null)
  );
}

function owner(node: TSESTree.Node): FunctionNode | null {
  let current = node.parent;
  while (current != null && !isFunction(current)) {
    if (current.type === "Program") return null;
    // Removing a lexical owner across finally can change observability.
    if (current.type === "TryStatement" && current.finalizer !== null)
      return null;
    current = current.parent;
  }
  return current ?? null;
}

function hasCallableShape(type: ts.Type): boolean {
  return type.isUnion()
    ? type.types.some(hasCallableShape)
    : type.getCallSignatures().length > 0 ||
        type.getConstructSignatures().length > 0;
}
