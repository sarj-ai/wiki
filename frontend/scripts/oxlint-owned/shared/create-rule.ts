import type {
  Context,
  CreateOnceRule,
  RuleMeta,
  Visitor,
} from "@oxlint/plugins";

const SELECTORS: readonly string[] = [
  "ReturnStatement",
  "BreakStatement",
  "ContinueStatement",
  "BlockStatement",
  "IfStatement",
  "FunctionDeclaration",
  "FunctionExpression",
  "ArrowFunctionExpression",
  "Program",
  "Program:exit",
  "BlockStatement:exit",
  "IfStatement:exit",
  "ConditionalExpression:exit",
  "SwitchStatement:exit",
  "BinaryExpression:exit",
  "LogicalExpression:exit",
  "CallExpression:exit",
  "AwaitExpression:exit",
  "FunctionDeclaration:exit",
  "FunctionExpression:exit",
  "ArrowFunctionExpression:exit",
  "ReturnStatement:exit",
  "BreakStatement:exit",
  "ContinueStatement:exit",
];

interface OwnedRule {
  name: string;
  meta: RuleMeta & {
    docs?: { description: string; requiresTypeChecking: boolean };
    defaultOptions: unknown[];
  };
  create(context: Context): Visitor;
}

export function createRule(
  definition: OwnedRule,
): CreateOnceRule & { meta: OwnedRule["meta"] } {
  const meta =
    definition.meta.docs === undefined
      ? definition.meta
      : {
          ...definition.meta,
          docs: {
            ...definition.meta.docs,
            url: `https://github.com/sarj-ai/wiki-internal/blob/main/frontend/scripts/oxlint-owned/rules/${definition.name}.ts`,
          },
        };
  return {
    meta,
    createOnce(context) {
      let visitor: Visitor = {};

      const dispatch: Visitor = {};
      for (const selector of SELECTORS)
        dispatch[selector] = (node) => visitor[selector]?.(node);
      return {
        ...dispatch,
        before() {
          visitor = definition.create(context);
        },
      };
    },
  };
}
