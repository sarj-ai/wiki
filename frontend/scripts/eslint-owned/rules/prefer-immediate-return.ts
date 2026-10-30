import { AST_NODE_TYPES, ESLintUtils, type TSESTree } from "@typescript-eslint/utils";
import ts from "typescript";

import { type FunctionNode, isFunction, unwrap } from "../shared/ast.ts";
import { hasDynamicEnvironment, onlyValueReads, variable } from "../shared/bindings.ts";
import { createRule } from "../shared/create-rule.ts";

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
      immediate: "{{operation}} this initializer directly; its temporary has no other value use.",
    },
  },
  create(context) {
    if (hasDynamicEnvironment(context.sourceCode)) return {};
    const services = ESLintUtils.getParserServices(context);
    const checker = services.program.getTypeChecker();
    const source = context.sourceCode;

    function checkPair(
      declaration: TSESTree.Statement | undefined,
      next: TSESTree.Statement | undefined,
    ): void {
      if (
        declaration?.type !== AST_NODE_TYPES.VariableDeclaration ||
        declaration.kind !== "const" ||
        declaration.declarations.length !== 1 ||
        (next?.type !== AST_NODE_TYPES.ReturnStatement &&
          next?.type !== AST_NODE_TYPES.ThrowStatement)
      )
        return;
      const entry = declaration.declarations[0];
      if (
        entry?.id.type !== AST_NODE_TYPES.Identifier ||
        entry.id.typeAnnotation !== undefined ||
        entry.init === null ||
        next.argument?.type !== AST_NODE_TYPES.Identifier ||
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
        (next.type === AST_NODE_TYPES.ReturnStatement && hasReturnContract(fn)) ||
        !sameValueContract(entry.init, entry.id)
      )
        return;
      context.report({
        node: entry.id,
        messageId: "immediate",
        data: { operation: next.type === AST_NODE_TYPES.ReturnStatement ? "Return" : "Throw" },
      });
    }

    function hasReturnContract(fn: FunctionNode): boolean {
      if (fn.returnType !== undefined) return true;
      const native = services.esTreeNodeToTSNodeMap.get(fn);
      if (
        (ts.isArrowFunction(native) || ts.isFunctionExpression(native)) &&
        checker.getContextualType(native) !== undefined
      )
        return true;
      if (fn.type === AST_NODE_TYPES.FunctionDeclaration && fn.id !== null) {
        const symbol = checker.getSymbolAtLocation(services.esTreeNodeToTSNodeMap.get(fn.id));
        if ((symbol?.getDeclarations()?.length ?? 0) > 1) return true;
      }
      return false;
    }

    function sameValueContract(
      initialNode: TSESTree.Node,
      localNode: TSESTree.Identifier,
    ): boolean {
      const initial = checker.getTypeAtLocation(services.esTreeNodeToTSNodeMap.get(initialNode));
      const local = checker.getTypeAtLocation(services.esTreeNodeToTSNodeMap.get(localNode));
      // Named evaluation can assign the binding's name to a function/class.
      // Keep all callable/constructable temporaries rather than erase it.
      if (hasCallableShape(initial) || hasCallableShape(local)) return false;
      const unknown =
        ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.Never | ts.TypeFlags.TypeParameter;
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
    node.type === AST_NODE_TYPES.TSAsExpression ||
    node.type === AST_NODE_TYPES.TSSatisfiesExpression ||
    node.type === AST_NODE_TYPES.TSTypeAssertion ||
    node.type === AST_NODE_TYPES.TSNonNullExpression
  )
    return true;
  const value = unwrap(node);
  return (
    value.type === AST_NODE_TYPES.ArrowFunctionExpression ||
    ((value.type === AST_NODE_TYPES.FunctionExpression ||
      value.type === AST_NODE_TYPES.ClassExpression) &&
      value.id === null)
  );
}

function owner(node: TSESTree.Node): FunctionNode | null {
  let current = node.parent;
  while (current !== undefined && !isFunction(current)) {
    if (current.type === AST_NODE_TYPES.Program) return null;
    // Removing a lexical owner across finally can change observability.
    if (current.type === AST_NODE_TYPES.TryStatement && current.finalizer !== null) return null;
    current = current.parent;
  }
  return current ?? null;
}

function hasCallableShape(type: ts.Type): boolean {
  return type.isUnion()
    ? type.types.some(hasCallableShape)
    : type.getCallSignatures().length > 0 || type.getConstructSignatures().length > 0;
}
