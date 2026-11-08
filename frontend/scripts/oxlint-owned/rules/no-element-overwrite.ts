import type { ESTree as TSESTree } from "@oxlint/plugins";
import { z } from "zod";

import {
  children,
  isFunction,
  unwrap,
  type Identifier,
} from "../shared/ast.ts";
import {
  hasDynamicEnvironment,
  initializedLexicalRead,
  noLaterVariableWrites,
  type Variable,
  variable,
} from "../shared/bindings.ts";
import { createRule } from "../shared/create-rule.ts";
import { propertyName as staticPropertyName } from "../shared/property-name.ts";
import {
  builtinConstructor,
  primitiveType,
  typedServices,
} from "../shared/types.ts";

const NumberLiteralSchema = z.union([
  z.number(),
  z.nan(),
  z.literal([Infinity, -Infinity]),
]);
const NegatableLiteralSchema = z.union([NumberLiteralSchema, z.bigint()]);
const StorageKeySchema = z.union([
  z.string().transform((value) => `string:${value}`),
  NumberLiteralSchema.transform((value) => `number:${String(value)}`),
  z.bigint().transform((value) => `bigint:${String(value)}`),
  z.boolean().transform((value) => `boolean:${String(value)}`),
  z.null().transform(() => "object:null"),
]);

type Kind = "array" | "Map" | "object" | "Set";
interface Storage {
  key: null | string;
  readonly kind: Kind;
  previous: null | TSESTree.Node;
}

function propertyName(node: TSESTree.MemberExpression): null | string {
  if (!node.computed && node.property.type === "Identifier")
    return node.property.name;
  const value = literalValue(node.property);
  return value === undefined ? null : String(value);
}

function literalValue(
  input: TSESTree.Node,
): bigint | boolean | null | number | string | undefined {
  const node = unwrap(input);
  if (node.type === "Literal" && !("regex" in node)) return node.value;
  if (node.type === "UnaryExpression" && node.operator === "-") {
    const value = literalValue(node.argument);
    const parsed = NegatableLiteralSchema.safeParse(value);
    if (parsed.success) return -parsed.data;
  }
  return undefined;
}

function executionOwner(node: TSESTree.Node): TSESTree.Node {
  let current = node;
  while (current.type !== "Program" && !isFunction(current)) {
    current = current.parent;
  }
  return current;
}

export default createRule({
  name: "no-element-overwrite",
  meta: {
    defaultOptions: [],
    type: "problem",
    docs: {
      description:
        "Report an unobserved repeated write to proven local data storage.",
      requiresTypeChecking: false,
    },
    schema: [],
    messages: {
      overwritten:
        "This element is written again before observation. Preserve insertion order when combining writes.",
    },
  },
  create(context) {
    const source = context.sourceCode;
    if (hasDynamicEnvironment(source)) return {};
    const services = typedServices(context);
    const checker = services?.program.getTypeChecker();
    const invalidNative = new Set<string>();
    let nativeDataPrototypeEscaped = false;
    const blocks: TSESTree.BlockStatement[] = [];
    const globalReceivers = new WeakMap<TSESTree.Node, boolean>();
    const valueReferences = new WeakSet<TSESTree.Node>();
    const sourceScopes = source.scopeManager.scopes;
    {
      for (const scope of sourceScopes) {
        for (const reference of scope.references)
          if (
            "isValueReference" in reference &&
            reference.isValueReference === true
          )
            valueReferences.add(reference.identifier);
      }
    }

    function inspectNativeUses(program: TSESTree.Program): void {
      const stack: TSESTree.Node[] = [program];
      while (stack.length > 0) {
        const node = stack.pop();
        if (node === undefined) break;
        if (node.type !== "Program") inspectNativeUse(node, node.parent);
        stack.push(...children(node, source));
      }
    }

    function inspectNativeUse(
      node: TSESTree.Node,
      parent: TSESTree.Node,
    ): void {
      const typeOnly =
        parent.type === "TSTypeReference" ||
        parent.type === "TSTypeQuery" ||
        parent.type === "TSQualifiedName";
      inspectNamespaceUse(
        node,
        parent,
        typeOnly ? null : nativeNamespace(node),
      );
      if (
        !typeOnly &&
        globalReceiver(node) &&
        (parent.type !== "MemberExpression" ||
          parent.object !== node ||
          staticPropertyName(parent, source.getScope(parent)) === null)
      )
        invalidateAllNative();
      if (
        node.type === "MemberExpression" &&
        ["getPrototypeOf", "setPrototypeOf", "__proto__"].includes(
          staticPropertyName(node, source.getScope(node)) ?? "",
        )
      ) {
        invalidateAllNative();
      }
    }

    function inspectNamespaceUse(
      node: TSESTree.Node,
      parent: TSESTree.Node,
      name: ReturnType<typeof nativeNamespace>,
    ): void {
      if (name === "Map" || name === "Set") {
        if (parent.type !== "NewExpression" || parent.callee !== node)
          invalidNative.add(name);
      } else if (name !== null) {
        if (parent.type !== "MemberExpression" || parent.object !== node)
          nativeDataPrototypeEscaped = true;
        else {
          const key = staticPropertyName(parent, source.getScope(parent));
          if (key === null) invalidateAllNative();
          else if (key === "prototype") nativeDataPrototypeEscaped = true;
        }
      }
    }

    function nativeNamespace(
      input: TSESTree.Node,
    ): "Array" | "Map" | "Object" | "Set" | null {
      const node = unwrap(input);
      if (node.type === "MemberExpression" && globalReceiver(node.object)) {
        const key = staticPropertyName(node, source.getScope(node));
        if (
          key === "Map" ||
          key === "Set" ||
          key === "Object" ||
          key === "Array"
        )
          return key;
      }
      if (
        node.type !== "Identifier" ||
        !valueReferences.has(node) ||
        (node.name !== "Map" &&
          node.name !== "Set" &&
          node.name !== "Object" &&
          node.name !== "Array")
      )
        return null;
      const binding = variable(node, source);
      return binding === null || binding.defs.length === 0 ? node.name : null;
    }

    function globalReceiver(input: TSESTree.Node): boolean {
      let node = input;
      const visited: TSESTree.Node[] = [];
      let result = false;
      for (;;) {
        const cached = globalReceivers.get(node);
        if (cached !== undefined) {
          result = cached;
          break;
        }
        visited.push(node);
        const value = unwrap(node);
        if (value !== node) {
          node = value;
          continue;
        }
        if (node.type === "ChainExpression") {
          node = node.expression;
          continue;
        }
        if (
          node.type === "MemberExpression" &&
          [
            "globalThis",
            "window",
            "self",
            "global",
            "top",
            "parent",
            "frames",
          ].includes(staticPropertyName(node, source.getScope(node)) ?? "")
        ) {
          node = node.object;
          continue;
        }
        if (
          node.type === "Identifier" &&
          valueReferences.has(node) &&
          ["globalThis", "window", "self", "global"].includes(node.name)
        ) {
          const binding = variable(node, source);
          result = binding === null || binding.defs.length === 0;
        }
        break;
      }
      for (const current of visited) globalReceivers.set(current, result);
      return result;
    }

    function invalidateAllNative(): void {
      nativeDataPrototypeEscaped = true;
      invalidNative.add("Map");
      invalidNative.add("Set");
    }

    function pureValue(input: TSESTree.Node): boolean {
      const node = unwrap(input);
      if (literalValue(node) !== undefined) return true;
      if (node.type === "Identifier") {
        const binding = variable(node, source);
        if (binding === null || !initializedLexicalRead(node, binding))
          return false;
        if (services !== null && checker !== undefined) {
          return primitiveType(
            checker.getTypeAtLocation(services.esTreeNodeToTSNodeMap.get(node)),
          );
        }
        const definition = binding.defs[0]?.node;
        return (
          noLaterVariableWrites(binding) &&
          definition?.type === "VariableDeclarator" &&
          definition.init !== null &&
          literalValue(definition.init) !== undefined
        );
      }
      return false;
    }

    function allocation(input: TSESTree.Node): Kind | null {
      const node = unwrap(input);
      if (
        node.type === "ArrayExpression" &&
        !nativeDataPrototypeEscaped &&
        node.elements.every((element) => element === null || pureValue(element))
      )
        return "array";
      if (
        node.type === "ObjectExpression" &&
        !nativeDataPrototypeEscaped &&
        node.properties.every(
          (property) =>
            property.type === "Property" &&
            property.kind === "init" &&
            !property.method &&
            !property.computed &&
            (property.key.type !== "Identifier" ||
              property.key.name !== "__proto__") &&
            (property.key.type !== "Literal" ||
              property.key.value !== "__proto__") &&
            pureValue(property.value),
        )
      )
        return "object";
      if (
        node.type === "NewExpression" &&
        node.callee.type === "Identifier" &&
        node.arguments.length === 0 &&
        services !== null
      ) {
        for (const name of ["Map", "Set"] as const) {
          if (
            !invalidNative.has(name) &&
            builtinConstructor(node.callee, name, services)
          )
            return name;
        }
      }
      return null;
    }

    function scan(block: TSESTree.BlockStatement | TSESTree.Program): void {
      const state = new Map<Variable, Storage>();
      for (const statement of block.body) {
        if (
          statement.type === "VariableDeclaration" &&
          statement.kind === "const"
        ) {
          recordDeclarations(statement, block, state);
          continue;
        }
        if (statement.type !== "ExpressionStatement") {
          state.clear();
          continue;
        }
        const expression = statement.expression;
        if (
          writeAssignment(expression, state) ||
          writeCollectionCall(expression, state)
        )
          continue;
        state.clear();
      }
    }

    function recordDeclarations(
      statement: TSESTree.VariableDeclaration,
      block: TSESTree.Node,
      state: Map<Variable, Storage>,
    ): void {
      if (
        statement.declarations.some(
          (entry) =>
            entry.id.type !== "Identifier" ||
            entry.init === null ||
            (allocation(entry.init) === null && !pureValue(entry.init)),
        )
      ) {
        state.clear();
        return;
      }
      for (const entry of statement.declarations) {
        if (entry.id.type !== "Identifier" || entry.init === null) continue;
        const kind = allocation(entry.init);
        const binding = stableLocal(entry.id, block);
        if (kind !== null && binding !== null)
          state.set(binding, { kind, key: null, previous: null });
      }
    }

    function stableLocal(
      id: Identifier,
      block: TSESTree.Node,
    ): null | Variable {
      const binding = variable(id, source);
      if (binding?.defs.length !== 1 || !noLaterVariableWrites(binding))
        return null;
      const owner = executionOwner(block);
      if (
        binding.references.some(
          (reference) => executionOwner(reference.identifier) !== owner,
        )
      )
        return null;
      return binding;
    }

    function writeAssignment(
      expression: TSESTree.Expression,
      state: Map<Variable, Storage>,
    ): boolean {
      if (
        expression.type !== "AssignmentExpression" ||
        expression.operator !== "=" ||
        expression.left.type !== "MemberExpression" ||
        expression.left.optional ||
        expression.left.object.type !== "Identifier" ||
        !pureValue(expression.right)
      )
        return false;
      const binding = variable(expression.left.object, source);
      const storage = binding === null ? undefined : state.get(binding);
      const key = propertyName(expression.left);
      if (
        storage === undefined ||
        (storage.kind !== "array" && storage.kind !== "object") ||
        key === null ||
        key === "__proto__" ||
        (storage.kind === "array" && key === "length")
      )
        return false;
      write(storage, key, expression.left);
      return true;
    }

    function writeCollectionCall(
      expression: TSESTree.Expression,
      state: Map<Variable, Storage>,
    ): boolean {
      if (
        expression.type !== "CallExpression" ||
        expression.optional ||
        expression.callee.type !== "MemberExpression" ||
        expression.callee.optional ||
        expression.callee.object.type !== "Identifier"
      )
        return false;
      const binding = variable(expression.callee.object, source);
      const storage = binding === null ? undefined : state.get(binding);
      const method = propertyName(expression.callee);
      const keyNode = expression.arguments[0];
      if (
        storage === undefined ||
        keyNode === undefined ||
        !(
          (storage.kind === "Map" &&
            method === "set" &&
            expression.arguments.length === 2) ||
          (storage.kind === "Set" &&
            method === "add" &&
            expression.arguments.length === 1)
        ) ||
        !expression.arguments.every(pureValue)
      )
        return false;
      const value = literalValue(keyNode);
      if (value === undefined) return false;
      write(storage, StorageKeySchema.parse(value), expression);
      return true;
    }

    function write(storage: Storage, key: string, node: TSESTree.Node): void {
      if (storage.key === key && storage.previous !== null)
        context.report({ node: storage.previous, messageId: "overwritten" });
      storage.key = key;
      storage.previous = node;
    }

    return {
      BlockStatement(node) {
        blocks.push(node);
      },
      "Program:exit"(program) {
        inspectNativeUses(program);
        for (const block of blocks) scan(block);
        scan(program);
      },
    };
  },
});
