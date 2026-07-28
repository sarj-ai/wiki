import { AST_NODE_TYPES, ESLintUtils } from "@typescript-eslint/utils";

const conventionalBucketExports = new Set(["cn"]);
const genericEntrypoints = new Set(["main", "run", "cli", "setup", "teardown", "execute"]);
const acronymOverrides = [
  [/OAuth/g, "Oauth"],
  [/GraphQL/g, "Graphql"],
  [/gRPC/g, "Grpc"],
];
const camelBoundary = /(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/g;
const generatedPath =
  /([\\/](?:generated|openapi-gen|graphql[\\/]types)[\\/])|(\.gen\.[cm]?[jt]sx?$)|(\.generated\.[cm]?[jt]sx?$)|(\.d\.[cm]?ts$)|(\.types\.[cm]?ts$)/;
const generatedMarker =
  /(?:@generated\b|generated (?:with|by)|generated (?:graphql )?types|do not edit(?: directly| manually)?)/i;

function basename(filename) {
  return filename.split(/[/\\]/).pop() ?? filename;
}

function stemOf(base) {
  return base
    .replace(/\.(test|spec|config|d|stories)\.[cm]?[jt]sx?$/i, "")
    .replace(/\.[cm]?[jt]sx?$/i, "");
}

function kebabCase(name) {
  let normalized = name;
  for (const [pattern, replacement] of acronymOverrides) {
    normalized = normalized.replace(pattern, replacement);
  }
  return normalized.replace(camelBoundary, "-").toLowerCase();
}

function isFunctionOrClass(node) {
  return (
    node.type === AST_NODE_TYPES.ArrowFunctionExpression ||
    node.type === AST_NODE_TYPES.FunctionExpression
  );
}

function isTestFile(filename) {
  const normalized = filename.replaceAll("\\", "/");
  const base = normalized.slice(normalized.lastIndexOf("/") + 1);
  if (
    /[.\-_](test|spec|e2e)\.[cm]?[jt]sx?$/.test(base) ||
    /\.integration\.[cm]?[jt]sx?$/.test(base)
  ) {
    return true;
  }
  return /(^|\/)(tests?|__tests__|__mocks__|fixtures|e2e|integration)\//.test(normalized);
}

function isGeneratedFile(filename, sourceText) {
  return (
    generatedPath.test(filename.replaceAll("\\", "/")) ||
    generatedMarker.test(sourceText.slice(0, 2048))
  );
}

function findPrimaryExport(body) {
  let exportCount = 0;
  let primaryCandidate = null;

  for (const statement of body) {
    if (statement.type === AST_NODE_TYPES.ExportAllDeclaration) return null;

    if (statement.type === AST_NODE_TYPES.ExportDefaultDeclaration) {
      exportCount += 1;
      const declaration = statement.declaration;
      if (
        (declaration.type === AST_NODE_TYPES.FunctionDeclaration ||
          declaration.type === AST_NODE_TYPES.ClassDeclaration) &&
        declaration.id !== null
      ) {
        primaryCandidate = { name: declaration.id.name, node: statement };
      } else if (declaration.type === AST_NODE_TYPES.Identifier) {
        primaryCandidate = { name: declaration.name, node: statement };
      }
      continue;
    }

    if (statement.type !== AST_NODE_TYPES.ExportNamedDeclaration) continue;
    if (statement.source !== null) return null;
    const declaration = statement.declaration;
    if (declaration === null) {
      exportCount += statement.specifiers.length;
      const [specifier] = statement.specifiers;
      if (
        statement.specifiers.length === 1 &&
        primaryCandidate === null &&
        specifier?.exported.type === AST_NODE_TYPES.Identifier
      ) {
        primaryCandidate = { name: specifier.exported.name, node: statement };
      }
    } else if (
      declaration.type === AST_NODE_TYPES.FunctionDeclaration ||
      declaration.type === AST_NODE_TYPES.ClassDeclaration
    ) {
      if (declaration.id !== null) {
        exportCount += 1;
        primaryCandidate ??= { name: declaration.id.name, node: statement };
      }
    } else if (declaration.type === AST_NODE_TYPES.VariableDeclaration) {
      for (const declarator of declaration.declarations) {
        if (declarator.id.type !== AST_NODE_TYPES.Identifier) continue;
        exportCount += 1;
        if (primaryCandidate === null && declarator.init && isFunctionOrClass(declarator.init)) {
          primaryCandidate = { name: declarator.id.name, node: statement };
        }
      }
    } else if (
      declaration.type === AST_NODE_TYPES.TSTypeAliasDeclaration ||
      declaration.type === AST_NODE_TYPES.TSInterfaceDeclaration
    ) {
      exportCount += 1;
      primaryCandidate ??= { name: declaration.id.name, node: statement };
    }
  }

  return primaryCandidate === null || exportCount > 2 ? null : primaryCandidate;
}

export default ESLintUtils.RuleCreator(
  (name) =>
    `https://github.com/sarj-ai/standards/blob/9d073e83b24cd5af788a61996cd9238d85d927d4/packages/typescript/src/rules/${name}.ts`,
)({
  name: "primary-export-file-name",
  meta: {
    type: "suggestion",
    docs: {
      description:
        "Enforce semantic naming alignment: a file with a single primary export must be named after that export.",
    },
    schema: [],
    messages: {
      primaryExportMismatch:
        "Module stem '{{stem}}' does not match its primary export '{{name}}' — rename the file to '{{expected}}{{ext}}' to describe its responsibility.",
    },
  },
  defaultOptions: [],
  create(context) {
    const base = basename(context.filename);
    if (base.endsWith(".d.ts") || base.startsWith("index.") || base.startsWith("_")) return {};
    if (
      isTestFile(context.filename) ||
      isGeneratedFile(context.filename, context.sourceCode.text)
    ) {
      return {};
    }

    const stem = stemOf(base);
    if (
      [
        "page",
        "layout",
        "loading",
        "error",
        "not-found",
        "route",
        "__root",
        "config",
        "constants",
        "types",
      ].includes(stem) ||
      stem.startsWith("$") ||
      stem.startsWith("[")
    ) {
      return {};
    }

    return {
      Program(node) {
        const primary = findPrimaryExport(node.body);
        if (primary === null) return;
        if (conventionalBucketExports.has(primary.name) || genericEntrypoints.has(primary.name)) {
          return;
        }
        const expectedStem = kebabCase(primary.name);
        if (stem === expectedStem) return;
        context.report({
          node: primary.node,
          messageId: "primaryExportMismatch",
          data: {
            stem,
            name: primary.name,
            expected: expectedStem,
            ext: base.endsWith(".tsx") ? ".tsx" : ".ts",
          },
        });
      },
    };
  },
});
