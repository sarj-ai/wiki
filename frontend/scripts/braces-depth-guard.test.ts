import assert from "node:assert/strict";
import { describe, it } from "node:test";

import braces, { type BracesNode } from "braces";

const nested = (opening: string, closing: string, depth: number): string =>
  `${opening.repeat(depth)}a${closing.repeat(depth)}`;

describe("braces security backport", () => {
  it("preserves ordinary expansion and invalid-brace escaping", () => {
    assert.deepEqual(braces.expand("src/{app,lib}/*.ts"), ["src/app/*.ts", "src/lib/*.ts"]);
    assert.equal(braces.stringify(braces.parse("{a}"), { escapeInvalid: true }), "{a}");
  });

  for (const [opening, closing] of [
    ["{", "}"],
    ["(", ")"],
  ] as const) {
    for (const method of ["parse", "compile", "expand", "stringify"] as const) {
      it(`bounds ${opening}${closing} nesting in ${method}`, () => {
        braces[method](nested(opening, closing, 100));
        assert.throws(
          () => braces[method](nested(opening, closing, 4000)),
          /exceeds max depth \(100\)/u,
        );
        assert.throws(
          () => braces[method](nested(opening, closing, 3), { maxDepth: 2.5 }),
          /exceeds max depth \(2\.5\)/u,
        );
        assert.throws(
          () => braces[method](nested(opening, closing, 101), { maxDepth: 10000 }),
          /exceeds max depth \(100\)/u,
        );
      });
    }
  }

  for (const method of ["compile", "expand", "stringify"] as const) {
    it(`bounds caller-supplied cyclic child graphs in ${method}`, () => {
      const root: BracesNode & { nodes: BracesNode[] } = { type: "root", nodes: [] };
      root.nodes.push(root);
      assert.throws(() => braces[method](root), /exceeds max depth \(100\)/u);
    });
  }

  it("rejects cyclic expansion parent chains", () => {
    const parent: BracesNode = { type: "paren", nodes: [], queue: [] };
    parent.parent = parent;
    const ast = { type: "paren", nodes: [], parent };
    assert.throws(() => braces.expand(ast), /AST parent chain contains a cycle/u);
  });
});
