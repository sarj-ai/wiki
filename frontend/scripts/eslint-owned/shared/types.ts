import { dirname, resolve } from "node:path";

import type { Context, ESTree } from "@oxlint/plugins";
import ts from "typescript";

import type { Identifier } from "./ast.ts";

interface Project {
  readonly config: ts.ParsedCommandLine;
  readonly sources: Map<string, string>;
  program: ts.Program;
}
const PROJECTS = new Map<string, Project>();

export function typedServices(context: Context) {
  if (context.settings["wiki-owned/untyped"] === true) return null;
  const filename = resolve(context.filename);
  const configPath = ts.findConfigFile(dirname(filename), (name) =>
    ts.sys.fileExists(name),
  );
  if (configPath === undefined) return null;
  const project = loadProject(configPath);
  updateCurrentSource(project, filename, context.sourceCode.text);
  const program = project.program;
  const source = program.getSourceFile(filename);
  if (source === undefined)
    throw new Error(`Compiler project omitted ${filename}`);
  const nodes = nodeRanges(source);
  return {
    program,
    esTreeNodeToTSNodeMap: {
      get(node: ESTree.Node): ts.Node {
        const native = nodes.get(`${node.range[0]}:${node.range[1]}`);
        if (native === undefined)
          throw new Error(
            `No compiler node for ${node.type} at ${filename}:${node.range.join(":")}`,
          );
        return native;
      },
    },
  };
}
function loadProject(configPath: string): Project {
  const configText = ts.sys.readFile(configPath);
  if (configText === undefined) throw new Error(`Cannot read ${configPath}`);
  const key = `${configPath}\0${configText}`;
  let project = PROJECTS.get(key);
  if (project === undefined) {
    const loaded = ts.parseConfigFileTextToJson(configPath, configText);
    if (loaded.error !== undefined)
      throw new Error(
        ts.flattenDiagnosticMessageText(loaded.error.messageText, "\n"),
      );
    const config = ts.parseJsonConfigFileContent(
      loaded.config,
      ts.sys,
      dirname(configPath),
    );
    const sources = new Map<string, string>();
    project = {
      config,
      sources,
      program: ts.createProgram(config.fileNames, config.options),
    };
    PROJECTS.set(key, project);
  }
  return project;
}

function updateCurrentSource(
  project: Project,
  filename: string,
  text: string,
): void {
  if (project.program.getSourceFile(filename)?.text !== text) {
    project.sources.set(filename, text);
    const host = ts.createCompilerHost(project.config.options);
    const read = host.readFile.bind(host);
    const current = project;
    host.readFile = (name) => current.sources.get(resolve(name)) ?? read(name);
    project.program = ts.createProgram({
      rootNames: [...new Set([...project.config.fileNames, filename])],
      options: project.config.options,
      host,
      oldProgram: project.program,
    });
  }
}

function nodeRanges(source: ts.SourceFile): Map<string, ts.Node> {
  const nodes = new Map<string, ts.Node>();
  function index(node: ts.Node): void {
    nodes.set(`${node.getStart(source)}:${node.end}`, node);
    if (ts.isFunctionDeclaration(node) && node.body !== undefined) {
      const asyncModifier = ts
        .getModifiers(node)
        ?.find((modifier) => modifier.kind === ts.SyntaxKind.AsyncKeyword);
      const keyword = node
        .getChildren(source)
        .find((child) => child.kind === ts.SyntaxKind.FunctionKeyword);
      const start = asyncModifier ?? keyword;
      if (start !== undefined)
        nodes.set(`${start.getStart(source)}:${node.end}`, node);
    }
    if (
      ts.isMethodDeclaration(node) ||
      ts.isGetAccessorDeclaration(node) ||
      ts.isSetAccessorDeclaration(node) ||
      ts.isConstructorDeclaration(node)
    ) {
      const token = node
        .getChildren(source)
        .find(
          (child) =>
            child.kind === ts.SyntaxKind.LessThanToken ||
            child.kind === ts.SyntaxKind.OpenParenToken,
        );
      if (token !== undefined)
        nodes.set(`${token.getStart(source)}:${node.end}`, node);
    }

    ts.forEachChild(node, index);
  }
  index(source);
  return nodes;
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
  node: Identifier,
  name: "Array" | "Map" | "Object" | "Set",
  services: TypedServices,
): boolean {
  const checker = services.program.getTypeChecker();
  const symbol = checker.getSymbolAtLocation(
    services.esTreeNodeToTSNodeMap.get(node),
  );
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

export function requireTypedServices(context: Context): TypedServices {
  const services = typedServices(context);
  if (services === null)
    throw new Error("Wiki typed rule requires a TypeScript project");
  return services;
}
