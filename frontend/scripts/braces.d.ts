// The repository's braces security patch adds maxDepth and cyclic-AST validation.
declare module "braces" {
  export interface BracesNode {
    nodes?: BracesNode[];
    parent?: BracesNode;
    queue?: unknown[];
    type: string;
  }

  interface Options {
    escapeInvalid?: boolean;
    maxDepth?: number;
  }

  interface Braces {
    compile(input: BracesNode | string, options?: Options): string;
    expand(input: BracesNode | string, options?: Options): string[];
    parse(input: string, options?: Options): BracesNode;
    stringify(input: BracesNode | string, options?: Options): string;
  }

  const braces: Braces;
  export default braces;
}
