import type { ESTree as TSESTree } from "@oxlint/plugins";
import { z } from "zod";

import { children, type FunctionNode, isFunction } from "../shared/ast.ts";
import { createRule } from "../shared/create-rule.ts";
import { equivalent } from "../shared/equivalent.ts";

const MinimumLinesSchema = z.number().default(3);

export default createRule({
  name: "no-identical-functions",
  meta: {
    defaultOptions: [3],
    type: "suggestion",
    docs: {
      description:
        "Report duplicate function implementations with the same bindings and runtime form.",
      requiresTypeChecking: false,
    },
    schema: [{ type: "integer", minimum: 3 }],
    messages: {
      duplicate: "This function repeats the implementation on line {{line}}.",
    },
  },
  create(context) {
    const minimumLines = MinimumLinesSchema.parse(context.options[0]);
    const source = context.sourceCode;
    const functions: FunctionNode[] = [];

    function receiverUse(node: FunctionNode): "meta" | "none" | "receiver" {
      let result: "none" | "receiver" = "none";
      const stack: TSESTree.Node[] = [...node.params, node.body];
      while (stack.length > 0) {
        const child = stack.pop();
        if (child === undefined) break;
        if (child.type === "MetaProperty") return "meta";
        if (child.type === "ThisExpression" || child.type === "Super")
          result = "receiver";
        if (isFunction(child) && child.type !== "ArrowFunctionExpression")
          continue;
        stack.push(...children(child, source));
      }
      return result;
    }

    function collect(node: TSESTree.Node): void {
      if (!isFunction(node)) return;
      if (
        node.body.type === "BlockStatement" &&
        receiverUse(node) !== "meta" &&
        enoughLines(node)
      )
        functions.push(node);
    }

    function enoughLines(node: FunctionNode): boolean {
      const lines = new Set<number>();
      for (const token of source.getTokens(node.body)) {
        if (
          token.range[0] === node.body.range[0] ||
          token.range[1] === node.body.range[1]
        )
          continue;
        for (
          let line = token.loc.start.line;
          line <= token.loc.end.line;
          line += 1
        )
          lines.add(line);
      }
      return lines.size >= minimumLines;
    }

    return {
      FunctionDeclaration: collect,
      FunctionExpression: collect,
      ArrowFunctionExpression: collect,
      "Program:exit"() {
        const prior: FunctionNode[] = [];
        for (const node of functions) {
          const duplicate = prior.find((first) => {
            if (
              first.type !== node.type ||
              form(first) !== form(node) ||
              first.async !== node.async ||
              (first.type !== "ArrowFunctionExpression" && first.generator) !==
                (node.type !== "ArrowFunctionExpression" && node.generator) ||
              first.params.length !== node.params.length
            )
              return false;
            if (
              (receiverUse(first) === "receiver" ||
                receiverUse(node) === "receiver") &&
              receiverOwner(first) !== receiverOwner(node)
            )
              return false;
            return equivalent(
              [
                ...first.params,
                ...(first.typeParameters == null ? [] : [first.typeParameters]),
                ...(first.returnType == null ? [] : [first.returnType]),
                first.body,
              ],
              [
                ...node.params,
                ...(node.typeParameters == null ? [] : [node.typeParameters]),
                ...(node.returnType == null ? [] : [node.returnType]),
                node.body,
              ],
              source,
              [first, node],
            );
          });
          if (duplicate === undefined) prior.push(node);
          else
            context.report({
              node:
                node.type === "ArrowFunctionExpression"
                  ? node
                  : (node.id ?? node),
              messageId: "duplicate",
              data: { line: String(duplicate.loc.start.line) },
            });
        }
      },
    };
  },
});

function receiverOwner(node: FunctionNode): null | TSESTree.Node {
  let current: TSESTree.Node = node;
  while (current.parent.type !== "Program") {
    current = current.parent;
    if (
      current.type === "ClassBody" ||
      current.type === "ObjectExpression" ||
      (isFunction(current) && current.type !== "ArrowFunctionExpression")
    )
      return current;
  }
  return null;
}

function form(node: FunctionNode): string {
  const parent = node.parent;
  if (parent.type === "MethodDefinition")
    return `${parent.kind}:${String(parent.static)}`;
  if (parent.type === "Property")
    return `${parent.kind}:${String(parent.method)}`;
  return node.type;
}
