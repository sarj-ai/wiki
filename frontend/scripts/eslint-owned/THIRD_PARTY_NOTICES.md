# MIT helper provenance

The typed child traversal, runtime-erased TypeScript-wrapper, stable initializer and public lexical binding patterns are adapted from these Code Standards files at commit b4e7f9019ca62a0861bda358a22b766ce313fe97:

- packages/typescript/src/rules/_for-each-ast-child.ts
- packages/typescript/src/rules/_unwrap-expression.ts
- packages/typescript/src/rules/_stable-value-binding.ts
- packages/typescript/src/rules/no-invalid-typed-mutation.ts (lexical native namespace/global receiver guard only)

The source inventory pins their whole byte identities. The general structural/binding comparator is independently authored here; it does not reuse duplicate-test-body or Sonar implementation code. The Code Standards package license follows.

MIT License

Copyright (c) 2026 sarj-ai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
