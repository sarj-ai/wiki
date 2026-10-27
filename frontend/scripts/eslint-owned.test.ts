import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, describe, it } from "node:test";

import { RuleTester } from "@typescript-eslint/rule-tester";
import tseslint from "typescript-eslint";

import compatiblePlugin, { rawPlugin as plugin } from "./eslint-owned/index.ts";

function reportRegistrationError(error: unknown): never {
  throw new Error("Node test registration failed", { cause: error });
}

RuleTester.afterAll = after;
RuleTester.describe = (name, callback) => {
  describe(name, callback).catch(reportRegistrationError);
};
RuleTester.it = (name, callback) => {
  it(name, callback).catch(reportRegistrationError);
};
RuleTester.itOnly = (name, callback) => {
  it.only(name, callback).catch(reportRegistrationError);
};

type RuleName = keyof typeof plugin.rules;
interface Requirement {
  readonly code: string;
  readonly diagnostics: number;
  readonly focus?: string;
  readonly id: string;
  readonly language?: "js" | "ts" | "tsx";
  readonly prefix: string;
  readonly rule: RuleName;
  readonly typed: boolean;
}

// The first32 code strings are the original independently authored challenge
// sources. Prefixes provide real, explicit type context; none is an observed
// result of a third-party algorithm.
const REQUIREMENTS: readonly Requirement[] = [
  {
    id: "no-all-duplicated-branches/complete-if",
    rule: "no-all-duplicated-branches",
    code: 'function choose(flag: boolean) { if (flag) { emit("fixed"); } else { emit("fixed"); } }',
    prefix: "declare function emit(value: unknown): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/conditional",
    rule: "no-all-duplicated-branches",
    code: "const choice = active ? readToken() : readToken();",
    prefix: "declare const active: boolean; declare function readToken(): string;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/different-branches",
    rule: "no-all-duplicated-branches",
    code: 'if (active) emit("active"); else emit("idle");',
    prefix: "declare const active: boolean; declare function emit(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/incomplete-switch",
    rule: "no-all-duplicated-branches",
    code: 'switch (kind) { case "a": emit("same"); break; case "b": emit("same"); break; }',
    prefix: "declare const kind: string; declare function emit(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-collapsible-if/nested-single",
    rule: "no-collapsible-if",
    code: "if (ready) { if (allowed) { deliver(); } }",
    prefix:
      "declare const ready: boolean; declare const allowed: boolean; declare function deliver(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-collapsible-if/outer-else",
    rule: "no-collapsible-if",
    code: "if (ready) { if (allowed) deliver(); } else { defer(); }",
    prefix:
      "declare const ready: boolean; declare const allowed: boolean; declare function deliver(): void; declare function defer(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-collapsible-if/intervening",
    rule: "no-collapsible-if",
    code: "if (ready) { mark(); if (allowed) deliver(); }",
    prefix:
      "declare const ready: boolean; declare const allowed: boolean; declare function deliver(): void; declare function mark(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-collapsible-if/effect-order",
    rule: "no-collapsible-if",
    code: "if (nextOuter()) { if (nextInner()) deliver(); }",
    prefix:
      "declare function nextOuter(): boolean; declare function nextInner(): boolean; declare function deliver(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/array-rewrite",
    rule: "no-element-overwrite",
    code: 'slots[2] = "draft"; slots[2] = "ready";',
    prefix: "const slots: string[] = [];",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/map-rewrite",
    rule: "no-element-overwrite",
    code: 'const entries = new Map<string, number>(); entries.set("item", 7); entries.set("item", 9);',
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/observed-between",
    rule: "no-element-overwrite",
    code: 'slots[2] = "draft"; display(slots[2]); slots[2] = "ready";',
    prefix: "declare function display(value: unknown): void; const slots: string[] = [];",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/custom-set",
    rule: "no-element-overwrite",
    code: 'class Cache { set(key: string, value: number) { log(key, value); } } const c = new Cache(); c.set("x", 1); c.set("x", 2);',
    prefix: "declare function log(...values: unknown[]): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/subtract",
    rule: "no-identical-expressions",
    code: "const delta = score - score;",
    prefix: "let score = 5;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-expressions/logical",
    rule: "no-identical-expressions",
    code: "const accepted = enabled && enabled;",
    prefix: "let enabled = true;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-expressions/side-effects",
    rule: "no-identical-expressions",
    code: "const answer = advance() - advance();",
    prefix: "declare function advance(): number;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/nan-and-mask",
    rule: "no-identical-expressions",
    code: "const nan = value !== value; const mask = 1 << 1;",
    prefix: "declare const value: number;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/duplicate-bodies",
    rule: "no-identical-functions",
    code: "function first() {\n  step();\n  finish();\n  return result;\n}\nfunction second() {\n  step();\n  finish();\n  return result;\n}",
    prefix:
      "declare function step(): void; declare function finish(): void; declare const result: string;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-functions/different-result",
    rule: "no-identical-functions",
    code: "function first() { step(); return 1; } function second() { step(); return 2; }",
    prefix: "declare function step(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/binding-shadow",
    rule: "no-identical-functions",
    code: "const load = sourceA; function first() { return load(); } { const load = sourceB; function second() { return load(); } }",
    prefix: "declare const sourceA: () => string; declare const sourceB: () => string;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/method-and-generator",
    rule: "no-identical-functions",
    code: "class Reader { *first() { yield this.next(); } *second() { yield this.next(); } }",
    prefix: "interface Reader { next(): number; }",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-redundant-jump/trailing-return",
    rule: "no-redundant-jump",
    code: "function notify() { emit(); return; }",
    prefix: "declare function emit(value?: unknown): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-redundant-jump/continue-end",
    rule: "no-redundant-jump",
    code: "for (const item of items) { emit(item); continue; }",
    prefix: "declare const items: readonly number[]; declare function emit(value: unknown): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-redundant-jump/early-return",
    rule: "no-redundant-jump",
    code: "function notify(skip: boolean) { if (skip) return; emit(); }",
    prefix: "declare function emit(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-redundant-jump/label-and-finally",
    rule: "no-redundant-jump",
    code: "outer: for (const item of items) { try { if (item) continue outer; } finally { finish(); } }",
    prefix: "declare const items: readonly number[]; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/void-assignment",
    rule: "no-use-of-empty-return-value",
    code: "function notify(): void { emit(); } const result = notify();",
    prefix: "declare function emit(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/void-argument",
    rule: "no-use-of-empty-return-value",
    code: "function notify(): void { emit(); } accept(notify());",
    prefix: "declare function emit(): void; declare function accept(value: unknown): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/statement-and-value",
    rule: "no-use-of-empty-return-value",
    code: 'function notify(): void { emit(); } notify(); function read(): string { return "ready"; } const result = read();',
    prefix: "declare function emit(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/contextual-void",
    rule: "no-use-of-empty-return-value",
    code: "const callback: () => void = () => 7; const result = callback();",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "prefer-immediate-return/return-temp",
    rule: "prefer-immediate-return",
    code: "function read() { const result = fetchValue(); return result; }",
    prefix: "declare function fetchValue(): string;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "prefer-immediate-return/throw-temp",
    rule: "prefer-immediate-return",
    code: 'function fail() { const problem = new Error("unavailable"); throw problem; }',
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "prefer-immediate-return/used-before-return",
    rule: "prefer-immediate-return",
    code: "function read() { const result = fetchValue(); log(result); return result; }",
    prefix: "declare function fetchValue(): string; declare function log(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/typed-temp",
    rule: "prefer-immediate-return",
    code: "function read() { const result: Narrow = obtainWide(); return result; }",
    prefix:
      "type Narrow = { key: string }; type Wide = Narrow & { extra: number }; declare function obtainWide(): Wide;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/switch-fallthrough",
    rule: "no-all-duplicated-branches",
    code: "switch (kind) { case 0: emit(); default: emit(); break; }",
    prefix: "declare const kind: number; declare function emit(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/switch-empty-terminal",
    rule: "no-all-duplicated-branches",
    code: "switch (kind) { case 0: emit(); break; default: emit(); break; case 1: }",
    prefix: "declare const kind: number; declare function emit(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/switch-complete",
    rule: "no-all-duplicated-branches",
    code: "switch (kind) { case 0: emit(); break; default: emit(); break; }",
    prefix: "declare const kind: number; declare function emit(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/grouped-labels",
    rule: "no-all-duplicated-branches",
    code: "switch (kind) { case 0: case 1: emit(); break; default: emit(); break; }",
    prefix: "declare const kind: number; declare function emit(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/local-binding-alpha",
    rule: "no-all-duplicated-branches",
    code: "if (flag) { const first = 1; emit(first); } else { const second = 1; emit(second); }",
    prefix: "declare const flag: boolean; declare function emit(value: unknown): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/inferred-name-observer",
    rule: "no-all-duplicated-branches",
    code: "if (flag) { const first = () => 1; emit(first.name); } else { const second = () => 1; emit(second.name); }",
    prefix: "declare const flag: boolean; declare function emit(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/regex-flags",
    rule: "no-all-duplicated-branches",
    code: "const value = flag ? /word/g : /word/i;",
    prefix: "declare const flag: boolean;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-collapsible-if/inner-else",
    rule: "no-collapsible-if",
    code: "if (ready) { if (allowed) deliver(); else defer(); }",
    prefix:
      "declare const ready: boolean; declare const allowed: boolean; declare function deliver(): void; declare function defer(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-collapsible-if/unbraced",
    rule: "no-collapsible-if",
    code: "if (ready) if (allowed) deliver();",
    prefix:
      "declare const ready: boolean; declare const allowed: boolean; declare function deliver(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/own-object",
    rule: "no-element-overwrite",
    code: "const box = { size: 0 }; box.size = 1; box.size = 2;",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/set-repeat",
    rule: "no-element-overwrite",
    code: 'const entries = new Set<string>(); entries.add("item"); entries.add("item");',
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/map-insertion-order",
    rule: "no-element-overwrite",
    code: 'const entries = new Map<string, number>(); entries.set("a", 1); entries.set("b", 2); entries.set("a", 3);',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/different-key-types",
    rule: "no-element-overwrite",
    code: 'const entries = new Map<number | bigint, string>(); entries.set(1, "first"); entries.set(1n, "second");',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/read-in-between",
    rule: "no-element-overwrite",
    code: "const slots = [0]; slots[0] = 1; const observed = slots[0]; slots[0] = 2;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/escape",
    rule: "no-element-overwrite",
    code: "const slots = [0]; slots[0] = 1; observe(slots); slots[0] = 2;",
    prefix: "declare function observe(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/alias",
    rule: "no-element-overwrite",
    code: "const slots = [0]; const alias = slots; slots[0] = 1; alias[0] = 2; slots[0] = 3;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/getter",
    rule: "no-element-overwrite",
    code: "const box = { set size(value: number) { observe(value); } }; box.size = 1; box.size = 2;",
    prefix: "declare function observe(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/rhs-effects",
    rule: "no-element-overwrite",
    code: "const slots = [0]; slots[0] = 1; slots[0] = read();",
    prefix: "declare function read(): number;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/map-prototype-replacement",
    rule: "no-element-overwrite",
    code: 'Map.prototype.set = function () { return this; }; const entries = new Map<string, number>(); entries.set("a", 1); entries.set("a", 2);',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/array-length",
    rule: "no-element-overwrite",
    code: "const slots = [0]; slots[0] = 1; slots.length = 0; slots[0] = 2;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/native-subclass",
    rule: "no-element-overwrite",
    code: 'class Entries extends Map<string, number> { override set(key: string, value: number): this { observe(value); return super.set(key, value); } } const entries = new Entries(); entries.set("a", 1); entries.set("a", 2);',
    prefix: "declare function observe(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/dynamic-write",
    rule: "no-element-overwrite",
    code: 'const slots = [0]; slots[0] = 1; eval("observe(slots[0])"); slots[0] = 2;',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/primitive-parameter",
    rule: "no-identical-expressions",
    code: "function read(value: number) { return value - value; }",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-expressions/literal",
    rule: "no-identical-expressions",
    code: "const result = 2 - 2;",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-expressions/getter-read",
    rule: "no-identical-expressions",
    code: "const counter = { get value() { return Math.random(); } }; const result = counter.value - counter.value;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/object-coercion",
    rule: "no-identical-expressions",
    code: "const counter = { valueOf() { return Math.random(); } }; const result = +counter - +counter;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/ambient-getter",
    rule: "no-identical-expressions",
    code: 'declare const sample: number; let reads = 0; Object.defineProperty(globalThis, "sample", { get: () => ++reads }); const delta = sample - sample;',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/meaningful-multiply",
    rule: "no-identical-expressions",
    code: "function square(value: number) { return value * value; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/dynamic-binding-write",
    rule: "no-identical-expressions",
    code: 'let sample = 1; eval("sample = { valueOf() { return Math.random(); } }"); const delta = sample - sample;',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/renamed-parameters",
    rule: "no-identical-functions",
    code: "function first(value: number) {\n const result = value + 1;\n emit(result);\n return result;\n}\nfunction second(input: number) {\n const result = input + 1;\n emit(result);\n return result;\n}",
    prefix: "declare function emit(value: unknown): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-functions/literal-values-retained",
    rule: "no-identical-functions",
    code: "function first() {\n step();\n finish();\n return 1;\n}\nfunction second() {\n step();\n finish();\n return 2;\n}",
    prefix: "declare function step(): void; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/free-bindings-retained",
    rule: "no-identical-functions",
    code: "const load = sourceA; function first() {\n step();\n finish();\n return load();\n}\n{ const load = sourceB; function second() {\n step();\n finish();\n return load();\n} }",
    prefix:
      "declare const sourceA: () => string; declare const sourceB: () => string; declare function step(): void; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/inferred-names-retained",
    rule: "no-identical-functions",
    code: "function first() {\n const apple = () => 1;\n emit(apple.name);\n return apple;\n}\nfunction second() {\n const orange = () => 1;\n emit(orange.name);\n return orange;\n}",
    prefix: "declare function emit(value: unknown): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/dynamic-names-retained",
    rule: "no-identical-functions",
    code: 'function first() {\n const apple = 1;\n const result = eval("apple");\n return result;\n}\nfunction second() {\n const orange = 1;\n const result = eval("apple");\n return result;\n}',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/new-target-retained",
    rule: "no-identical-functions",
    code: "function first() {\n step();\n finish();\n return new.target;\n}\nfunction second() {\n step();\n finish();\n return new.target;\n}",
    prefix: "declare function step(): void; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/async-form-retained",
    rule: "no-identical-functions",
    code: "function first() {\n step();\n finish();\n return 1;\n}\nasync function second() {\n step();\n finish();\n return 1;\n}",
    prefix: "declare function step(): void; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/blank-lines-not-code",
    rule: "no-identical-functions",
    code: "function first() {\n\n step();\n\n return 1;\n}\nfunction second() {\n step();\n\n return 1;\n}",
    prefix: "declare function step(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-redundant-jump/single-block-stub",
    rule: "no-redundant-jump",
    code: "function notify() { return; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-redundant-jump/necessary-loop-return",
    rule: "no-redundant-jump",
    code: "function choose(items: number[]) { for (const item of items) { if (item) return; } }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-redundant-jump/effect-after-continue",
    rule: "no-redundant-jump",
    code: "for (const item of items) { if (item) { emit(item); continue; } finish(); }",
    prefix:
      "declare const items: readonly number[]; declare function emit(value: unknown): void; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-redundant-jump/label-end",
    rule: "no-redundant-jump",
    code: "done: { emit(); break done; }",
    prefix: "declare function emit(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/effect-only-logical",
    rule: "no-use-of-empty-return-value",
    code: "function log() {} flag && log();",
    prefix: "declare const flag: boolean;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/effect-only-conditional",
    rule: "no-use-of-empty-return-value",
    code: "function log() {} flag ? log() : log();",
    prefix: "declare const flag: boolean;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/explicit-discard-logical",
    rule: "no-use-of-empty-return-value",
    code: "function log() {} void (flag && log());",
    prefix: "declare const flag: boolean;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/await-discard-logical",
    rule: "no-use-of-empty-return-value",
    code: "function log() {} await (flag && log());",
    prefix: "declare const flag: boolean;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/void-used-as-condition",
    rule: "no-use-of-empty-return-value",
    code: "function log() {} const result = log() ? 1 : 2;",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/meaningful-promise",
    rule: "no-use-of-empty-return-value",
    code: "async function log(): Promise<void> {} const pending = log();",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/awaited-empty-value",
    rule: "no-use-of-empty-return-value",
    code: "async function log(): Promise<void> {} const value = await log();",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/awaited-for-effects",
    rule: "no-use-of-empty-return-value",
    code: "async function log(): Promise<void> {} await log();",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/explicit-undefined-value",
    rule: "no-use-of-empty-return-value",
    code: "function read(): undefined { return undefined; } const result = read();",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/mixed-contract",
    rule: "no-use-of-empty-return-value",
    code: "declare function read(): void | number; const result = read();",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/return-delegation",
    rule: "no-use-of-empty-return-value",
    code: "function log(): void {} function notify(): void { return log(); }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/untyped-empty-body",
    rule: "no-use-of-empty-return-value",
    code: "function log() {} const result = log();",
    prefix: "",
    diagnostics: 1,
    typed: false,
  },
  {
    id: "no-use-of-empty-return-value/untyped-alias",
    rule: "no-use-of-empty-return-value",
    code: "function log() {} const notify = log; const result = notify();",
    prefix: "",
    diagnostics: 1,
    typed: false,
  },
  {
    id: "no-use-of-empty-return-value/untyped-dynamic-write",
    rule: "no-use-of-empty-return-value",
    code: 'function log() {} eval("log = () => 42"); const result = log();',
    prefix: "",
    diagnostics: 0,
    typed: false,
  },
  {
    id: "prefer-immediate-return/explicit-return-contract",
    rule: "prefer-immediate-return",
    code: "function read(): { value: number } { const result = { value: 1, extra: true }; return result; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/contextual-return-contract",
    rule: "prefer-immediate-return",
    code: "const read: () => { value: number } = () => { const result = { value: 1, extra: true }; return result; };",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/finalizer-lifetime",
    rule: "prefer-immediate-return",
    code: "function read() { try { const result = getValue(); return result; } finally { finish(); } }",
    prefix: "declare function getValue(): string; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/additional-reference",
    rule: "prefer-immediate-return",
    code: "function read() { const result = getValue(); const copy = result; return result; }",
    prefix: "declare function getValue(): string;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/dynamic-name-use",
    rule: "prefer-immediate-return",
    code: 'function read() { const result = eval("typeof result"); return result; }',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/nested-await-single-diagnostic",
    rule: "no-use-of-empty-return-value",
    code: "function effect(): void {} const value = await (await effect());",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/erased-await-single-diagnostic",
    rule: "no-use-of-empty-return-value",
    code: "function effect(): void {} const value = await (effect() as void);",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-use-of-empty-return-value/erased-unknown-still-consumed",
    rule: "no-use-of-empty-return-value",
    code: "function effect(): void {} const value = await (effect() as unknown);",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-functions/erased-direct-eval",
    rule: "no-identical-functions",
    code: 'function first() {\n const apple = 1;\n const result = (eval as typeof eval)("apple");\n return result;\n}\nfunction second() {\n const orange = 1;\n const result = (eval as typeof eval)("apple");\n return result;\n}',
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-all-duplicated-branches/tsx-duplicate",
    rule: "no-all-duplicated-branches",
    code: "const view = active ? <button>Send</button> : <button>Send</button>;",
    prefix: "declare const active: boolean;",
    diagnostics: 1,
    typed: true,
    language: "tsx",
  },
  {
    id: "no-all-duplicated-branches/tsx-distinct",
    rule: "no-all-duplicated-branches",
    code: "const view = active ? <button>Send</button> : <button>Save</button>;",
    prefix: "declare const active: boolean;",
    diagnostics: 0,
    typed: true,
    language: "tsx",
  },
  {
    id: "no-identical-expressions/javascript-local",
    rule: "no-identical-expressions",
    code: "let score = 5; const delta = score - score;",
    prefix: "",
    diagnostics: 1,
    typed: false,
    language: "js",
  },
  {
    id: "no-all-duplicated-branches/diagnostic-location",
    rule: "no-all-duplicated-branches",
    code: "if (flag) { emit(); } else { emit(); }",
    prefix: "declare const flag: boolean; declare function emit(): void;",
    diagnostics: 1,
    typed: true,
    focus: "flag",
  },
  {
    id: "no-collapsible-if/diagnostic-location",
    rule: "no-collapsible-if",
    code: "if (ready) { if (allowed) deliver(); }",
    prefix:
      "declare const ready: boolean; declare const allowed: boolean; declare function deliver(): void;",
    diagnostics: 1,
    typed: true,
    focus: "allowed",
  },
  {
    id: "no-element-overwrite/diagnostic-location",
    rule: "no-element-overwrite",
    code: "const box = { size: 0 };\nbox.size = 1;\nbox.size = 2;",
    prefix: "",
    diagnostics: 1,
    typed: true,
    focus: "box.size = 1",
  },
  {
    id: "no-identical-expressions/diagnostic-location",
    rule: "no-identical-expressions",
    code: "const value = 2 - 2;",
    prefix: "",
    diagnostics: 1,
    typed: true,
    focus: "2 - 2",
  },
  {
    id: "no-identical-functions/diagnostic-location",
    rule: "no-identical-functions",
    code: "function first() {\n step();\n finish();\n return 1;\n}\nfunction second() {\n step();\n finish();\n return 1;\n}",
    prefix: "declare function step(): void; declare function finish(): void;",
    diagnostics: 1,
    typed: true,
    focus: "second()",
  },
  {
    id: "no-redundant-jump/diagnostic-location",
    rule: "no-redundant-jump",
    code: "function notify() { emit(); return; }",
    prefix: "declare function emit(): void;",
    diagnostics: 1,
    typed: true,
    focus: "return;",
  },
  {
    id: "no-use-of-empty-return-value/diagnostic-location",
    rule: "no-use-of-empty-return-value",
    code: "function notify(): void {} const value = notify();",
    prefix: "",
    diagnostics: 1,
    typed: true,
    focus: "notify();",
  },
  {
    id: "prefer-immediate-return/diagnostic-location",
    rule: "prefer-immediate-return",
    code: "function read() { const result = getValue(); return result; }",
    prefix: "declare function getValue(): string;",
    diagnostics: 1,
    typed: true,
    focus: "result =",
  },
  {
    id: "prefer-immediate-return/inferred-arrow-name",
    rule: "prefer-immediate-return",
    code: "function make() { const callback = () => 1; return callback; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/inferred-function-name",
    rule: "prefer-immediate-return",
    code: "function make() { const callback = function () { return 1; }; return callback; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/inferred-class-name",
    rule: "prefer-immediate-return",
    code: "function make() { const Entry = class {}; return Entry; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "prefer-immediate-return/inferred-private-constructor-class-name",
    rule: "prefer-immediate-return",
    code: "function make() { const Entry = class { private constructor() {} }; return Entry; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/first-bigint-rhs-throws",
    rule: "no-element-overwrite",
    code: "const box = { value: 0n }; box.value = 1n / 0n; box.value = 2n;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/later-bigint-rhs-throws",
    rule: "no-element-overwrite",
    code: "const box = { value: 0n }; box.value = 1n; box.value = 1n / 0n;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/mixed-rhs-throws",
    rule: "no-element-overwrite",
    code: "const box = { value: 0n }; box.value = 1n + 1; box.value = 2n;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/unary-bigint-rhs-throws",
    rule: "no-element-overwrite",
    code: "const box = { value: 0n }; box.value = +1n; box.value = 2n;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/rhs-tdz",
    rule: "no-element-overwrite",
    code: "const box = { value: 0 }; box.value = later; box.value = 2; const later = 1;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/hoisted-capture-tdz",
    rule: "no-element-overwrite",
    code: "run(); const later = 1; function run() { const box = { value: 0 }; box.value = later; box.value = 2; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/initialized-parameter-value",
    rule: "no-element-overwrite",
    code: "function run(value: number) { const box = { value: 0 }; box.value = value; box.value = 2; }",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/negative-literal-values",
    rule: "no-element-overwrite",
    code: "const box = { value: 0 }; box.value = -1; box.value = -2;",
    prefix: "",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/untyped-array-prototype-setter",
    rule: "no-element-overwrite",
    code: 'Object.defineProperty(Array.prototype, "2", { set(value) { observe(value); } }); const slots = []; slots[2] = 1; slots[2] = 2;',
    prefix: "function observe(value) {}",
    diagnostics: 0,
    typed: false,
    language: "js",
  },
  {
    id: "no-element-overwrite/computed-global-map-prototype",
    rule: "no-element-overwrite",
    code: 'globalThis["Map"].prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/global-namespace-alias",
    rule: "no-element-overwrite",
    code: 'const root = globalThis; root.Map.prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/reflected-array-prototype",
    rule: "no-element-overwrite",
    code: 'Object.defineProperty(Object.getPrototypeOf([]), "2", { set(value) { observe(value); } }); const slots = []; slots[2] = 1; slots[2] = 2;',
    prefix: "function observe(value) {}",
    diagnostics: 0,
    typed: false,
    language: "js",
  },
  {
    id: "no-element-overwrite/computed-prototype-key",
    rule: "no-element-overwrite",
    code: 'const key = "__proto__"; Object.defineProperty([][key], "2", { set(value) { observe(value); } }); const slots = []; slots[2] = 1; slots[2] = 2;',
    prefix: "function observe(value) {}",
    diagnostics: 0,
    typed: false,
    language: "js",
  },
  {
    id: "no-element-overwrite/custom-object-setter-does-not-touch-intrinsic",
    rule: "no-element-overwrite",
    code: 'const custom = {}; Object.defineProperty(custom, "2", { set(value) { observe(value); } }); const slots = []; slots[2] = 1; slots[2] = 2;',
    prefix: "function observe(value) {}",
    diagnostics: 1,
    typed: false,
    language: "js",
  },
  {
    id: "no-element-overwrite/shadowed-array-is-not-intrinsic",
    rule: "no-element-overwrite",
    code: 'class Array {} Object.defineProperty(Array.prototype, "2", { set(value) { observe(value); } }); const slots = []; slots[2] = 1; slots[2] = 2;',
    prefix: "function observe(value) {}",
    diagnostics: 1,
    typed: false,
    language: "js",
  },
  {
    id: "no-identical-expressions/tdz-read",
    rule: "no-identical-expressions",
    code: "const result = later - later; const later = 1;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/hoisted-capture-read",
    rule: "no-identical-expressions",
    code: "run(); const later = 1; function run() { return later - later; }",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-expressions/erased-object-is-not-primitive",
    rule: "no-identical-expressions",
    code: "const value = { valueOf() { return Math.random(); } } as unknown as number; const result = value - value;",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/distinct-receiver-homes",
    rule: "no-identical-functions",
    code: "class First { value = 1; read() {\n step();\n finish();\n return this.value;\n} }\nclass Second { value = 2; read() {\n step();\n finish();\n return this.value;\n} }",
    prefix: "declare function step(): void; declare function finish(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/same-receiver-home",
    rule: "no-identical-functions",
    code: "class Reader { value = 1; first() {\n step();\n finish();\n return this.value;\n}\nsecond() {\n step();\n finish();\n return this.value;\n} }",
    prefix: "declare function step(): void; declare function finish(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-functions/same-generator-home",
    rule: "no-identical-functions",
    code: "class Reader { value = 1; *first() {\n step();\n finish();\n yield this.value;\n}\n*second() {\n step();\n finish();\n yield this.value;\n} }",
    prefix: "declare function step(): void; declare function finish(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-identical-functions/inferred-type-contracts-retained",
    rule: "no-identical-functions",
    code: "function first(value: object) {\n step();\n finish();\n return value as Accepted;\n}\nfunction second(value: object) {\n step();\n finish();\n return value as Rejected;\n}",
    prefix:
      'type Accepted = { kind: "accepted" }; type Rejected = { kind: "rejected" }; declare function step(): void; declare function finish(): void;',
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/cross-case-tdz-read",
    rule: "no-element-overwrite",
    code: "function run(kind) { switch (kind) { case 0: const value = 1; break; case 1: { const box = { value: 0 }; box.value = value; box.value = 2; } } } run(1);",
    prefix: "",
    diagnostics: 0,
    typed: false,
    language: "js",
  },
  {
    id: "no-identical-expressions/cross-case-tdz-read",
    rule: "no-identical-expressions",
    code: "function run(kind: number) { switch (kind) { case 0: const value = 1; break; case 1: return value - value; } } run(1);",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/enclosing-initialization-before-switch",
    rule: "no-element-overwrite",
    code: "function run(kind) { const value = 1; switch (kind) { case 0: break; case 1: { const box = { value: 0 }; box.value = value; box.value = 2; } } }",
    prefix: "",
    diagnostics: 1,
    typed: false,
    language: "js",
  },
  {
    id: "no-element-overwrite/ordinary-block-within-case-initialized",
    rule: "no-element-overwrite",
    code: "function run(kind) { switch (kind) { case 0: break; case 1: { const value = 1; const box = { value: 0 }; box.value = value; box.value = 2; } } }",
    prefix: "",
    diagnostics: 1,
    typed: false,
    language: "js",
  },
  {
    id: "no-element-overwrite/optional-global-map-alias",
    rule: "no-element-overwrite",
    code: 'const Native = globalThis?.Map; Native.prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/chained-global-map-alias",
    rule: "no-element-overwrite",
    code: 'const Native = globalThis.globalThis.Map; Native.prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/computed-global-chain-map-alias",
    rule: "no-element-overwrite",
    code: 'const Native = globalThis["globalThis"]["Map"]; Native.prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/parenthesized-optional-global-chain",
    rule: "no-element-overwrite",
    code: 'const Native = (globalThis?.globalThis).Map; Native.prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/optional-computed-global-map-alias",
    rule: "no-element-overwrite",
    code: 'const Native = globalThis?.["Map"]; Native.prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/shadowed-optional-global-namespace",
    rule: "no-element-overwrite",
    code: 'class Local { set() { return this; } } const globalThis = { Map: Local }; globalThis?.Map.prototype.set = function () { observe(); return this; }; const entries = new Map<string, number>(); entries.set("item", 1); entries.set("item", 2);',
    prefix: "declare function observe(): void;",
    diagnostics: 1,
    typed: true,
  },
  {
    id: "no-element-overwrite/optional-global-set-alias",
    rule: "no-element-overwrite",
    code: "const Native = globalThis?.Set; Native.prototype.add = function () { observe(); return this; }; const entries = new Set<number>(); entries.add(1); entries.add(1);",
    prefix: "declare function observe(): void;",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-element-overwrite/optional-global-object-prototype-setter",
    rule: "no-element-overwrite",
    code: 'Object.defineProperty(globalThis?.Object.prototype, "2", { set(value) { observe(value); } }); const slots = []; slots[2] = 1; slots[2] = 2;',
    prefix: "function observe(value) {}",
    diagnostics: 0,
    typed: false,
    language: "js",
  },
  {
    id: "no-identical-functions/default-super-home",
    rule: "no-identical-functions",
    code: "class First extends ParentA {\n run(value = super.read()) {\n  const a = value + 1;\n  const b = a + 1;\n  return b;\n }\n}\nclass Second extends ParentB {\n run(value = super.read()) {\n  const a = value + 1;\n  const b = a + 1;\n  return b;\n }\n}",
    prefix:
      "class ParentA { read(): number { return 1; } } class ParentB { read(): number { return 2; } }",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/default-this-home",
    rule: "no-identical-functions",
    code: "class First {\n value = 1;\n run(value = this.value) {\n  const a = value + 1;\n  const b = a + 1;\n  return b;\n }\n}\nclass Second {\n value = 2;\n run(value = this.value) {\n  const a = value + 1;\n  const b = a + 1;\n  return b;\n }\n}",
    prefix: "",
    diagnostics: 0,
    typed: true,
  },
  {
    id: "no-identical-functions/default-new-target",
    rule: "no-identical-functions",
    code: "class First {\n constructor(value = new.target) {\n  const name = value.name;\n  const size = name.length;\n  accept(size);\n }\n}\nclass Second {\n constructor(value = new.target) {\n  const name = value.name;\n  const size = name.length;\n  accept(size);\n }\n}",
    prefix: "declare function accept(value: number): void;",
    diagnostics: 0,
    typed: true,
  },
];

const TEMPORARY_BASE = realpathSync(tmpdir());
if (process.env["TMPDIR"] !== undefined)
  assert.equal(realpathSync(process.env["TMPDIR"]), TEMPORARY_BASE);
for (let directory = TEMPORARY_BASE; ; directory = dirname(directory)) {
  assert.equal(existsSync(join(directory, ".git")), false, `Unexpected Git marker in ${directory}`);
  assert.equal(
    existsSync(join(directory, "pyproject.toml")),
    false,
    `Unexpected Python project marker in ${directory}`,
  );
  if (dirname(directory) === directory) break;
}
const FIXTURE_ROOT = mkdtempSync(join(TEMPORARY_BASE, "wiki-owned-rules-"));
const PROBE = join(FIXTURE_ROOT, "private-io-probe");
writeFileSync(PROBE, "owned", { flag: "wx", mode: 0o600 });
assert.equal(readFileSync(PROBE, "utf8"), "owned");
unlinkSync(PROBE);
writeFileSync(
  join(FIXTURE_ROOT, "tsconfig.json"),
  JSON.stringify({
    compilerOptions: {
      target: "ES2025",
      module: "ESNext",
      moduleResolution: "Bundler",
      strict: true,
      noEmit: true,
      allowJs: true,
      jsx: "preserve",
      types: [],
      lib: ["ES2025", "DOM"],
    },
    include: ["*.ts", "*.tsx", "*.js"],
  }),
  { flag: "wx", mode: 0o600 },
);

let nextFixture = 0;
function fixtureCase(requirement: Requirement) {
  const name = `case-${String(nextFixture++)}.${requirement.language ?? "ts"}`;
  const filename = join(FIXTURE_ROOT, name);
  const code = `export {};\n${requirement.prefix}\n${requirement.code}`;
  writeFileSync(filename, code, { flag: "wx", mode: 0o600 });
  return {
    code,
    filename: requirement.typed ? name : filename,
    languageOptions: {
      parserOptions: {
        project: requirement.typed ? "./tsconfig.json" : false,
        tsconfigRootDir: FIXTURE_ROOT,
      },
    },
  };
}

function casesFor<M extends string>(name: RuleName, messageId: M) {
  const selected = REQUIREMENTS.filter((requirement) => requirement.rule === name);
  assert.ok(selected.length >= 4, `Missing independent requirements for ${name}`);
  return {
    valid: selected.filter((requirement) => requirement.diagnostics === 0).map(fixtureCase),
    invalid: selected
      .filter((requirement) => requirement.diagnostics > 0)
      .map((requirement) => ({
        ...fixtureCase(requirement),
        errors: Array.from({ length: requirement.diagnostics }, () => {
          if (requirement.focus === undefined) return { messageId };
          const start = requirement.code.indexOf(requirement.focus);
          assert.ok(start >= 0 && !requirement.code.includes(requirement.focus, start + 1));
          const before = requirement.code.slice(0, start).split("\n");
          return {
            messageId,
            line: requirement.prefix.split("\n").length + 1 + before.length,
            column: (before.at(-1)?.length ?? 0) + 1,
          };
        }),
        output: null,
      })),
  };
}

const TESTER = new RuleTester({
  languageOptions: {
    parser: tseslint.parser,
    parserOptions: { ecmaVersion: 2025, sourceType: "module" },
  },
});
TESTER.run(
  "no-all-duplicated-branches",
  plugin.rules["no-all-duplicated-branches"],
  casesFor("no-all-duplicated-branches", "duplicated"),
);
TESTER.run(
  "no-collapsible-if",
  plugin.rules["no-collapsible-if"],
  casesFor("no-collapsible-if", "combine"),
);
TESTER.run(
  "no-element-overwrite",
  plugin.rules["no-element-overwrite"],
  casesFor("no-element-overwrite", "overwritten"),
);
TESTER.run(
  "no-identical-expressions",
  plugin.rules["no-identical-expressions"],
  casesFor("no-identical-expressions", "identical"),
);
TESTER.run(
  "no-identical-functions",
  plugin.rules["no-identical-functions"],
  casesFor("no-identical-functions", "duplicate"),
);
TESTER.run(
  "no-redundant-jump",
  plugin.rules["no-redundant-jump"],
  casesFor("no-redundant-jump", "redundant"),
);
TESTER.run(
  "no-use-of-empty-return-value",
  plugin.rules["no-use-of-empty-return-value"],
  casesFor("no-use-of-empty-return-value", "empty"),
);
TESTER.run(
  "prefer-immediate-return",
  plugin.rules["prefer-immediate-return"],
  casesFor("prefer-immediate-return", "immediate"),
);

it("exports only the eight explicit Wiki rules without autofixes", () => {
  assert.deepEqual(Object.keys(plugin.rules).toSorted(), [
    "no-all-duplicated-branches",
    "no-collapsible-if",
    "no-element-overwrite",
    "no-identical-expressions",
    "no-identical-functions",
    "no-redundant-jump",
    "no-use-of-empty-return-value",
    "prefer-immediate-return",
  ]);
  assert.equal(REQUIREMENTS.length, new Set(REQUIREMENTS.map((entry) => entry.id)).size);
  for (const [name, rule] of Object.entries(plugin.rules)) {
    assert.equal(rule.meta.fixable, undefined);
    assert.notEqual(rule.meta.hasSuggestions, true);
    assert.deepEqual(rule.meta.defaultOptions, name === "no-identical-functions" ? [3] : []);
  }
  assert.deepEqual(plugin.rules["no-identical-functions"].meta.schema, [
    { type: "integer", minimum: 3 },
  ]);
  const docs = plugin.rules["prefer-immediate-return"].meta.docs;
  assert.ok(docs !== undefined);
  assert.equal(docs.requiresTypeChecking, true);
});

after(() => {
  rmSync(FIXTURE_ROOT, { recursive: true });
});

it("adapts every typed rule without changing its metadata", () => {
  const adaptedRules = compatiblePlugin.rules;
  assert.ok(adaptedRules);
  assert.deepEqual(Object.keys(adaptedRules).toSorted(), Object.keys(plugin.rules).toSorted());
  for (const [name, rule] of Object.entries(plugin.rules)) {
    const adapted: (typeof adaptedRules)[string] | undefined = adaptedRules[name];
    assert.ok(adapted);
    assert.equal(adapted.meta, rule.meta);
    const adaptedCreate: unknown = Object.getOwnPropertyDescriptor(adapted, "create")?.value;
    const originalCreate: unknown = Object.getOwnPropertyDescriptor(rule, "create")?.value;
    assert.equal(typeof adaptedCreate, "function");
    assert.equal(typeof originalCreate, "function");
    assert.notEqual(adaptedCreate, originalCreate);
  }
});
