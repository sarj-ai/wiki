# Wiki owned quality rules

This local ESLint plugin implements eight explicit Wiki checks. It exports no
recommended preset and changes no Code Standards fleet defaults. The rules do
not offer automatic fixes.

The implementations and behavioral cases are first-party work. They do not
import, execute, or copy Sonar rule implementations. Selected typed AST and
binding primitives are adapted from the MIT Code Standards sources identified
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The test-body comparator,
which normalizes some literal values, is deliberately not used.

The current source is a prototype for independent behavioral, false-positive,
corpus and performance review. It is not a claim of equivalence to an unexecuted
third-party algorithm. Migration must preserve unrelated rules, the eight error
severities, existing baseline entries and native test limits. No license waiver
is included. Full application qualification follows normal dependency and
generated-config migration to the published Core release without Sonar.

Binding-based value proofs abstain in files with direct eval or with, which can
read or write names without static references. Alpha comparison preserves names
that supply an inferred function or class name and does not rename locals in
dynamic-name contexts. Primitive types alone do not establish stable reads:
ambient/foreign bindings and script-global var storage are excluded.

## no-all-duplicated-branches

Reports complete if/else chains, conditional alternatives and complete switches
whose implementations have the same operations, exact literal values and value
bindings. Local binding renaming must preserve lexical scope. Incomplete
switches, empty terminal alternatives and fallthrough do not establish equal
complete alternatives. Conditions may have effects; a report does not authorize
deleting or reordering their evaluation.

## no-collapsible-if

Reports a single nested if with no alternatives or intervening statements.
Conjunction must retain outer-then-inner short-circuit evaluation. Meaningful
comments and conditions remain source owned; there is no automatic rewrite.

## no-element-overwrite

Tracks straight-line repeated writes to fresh, local, non-exported const arrays
and plain own-data objects. With type services it also recognizes fresh native
Map and Set constructors by their standard-library symbols. Custom classes,
subclasses, constructor aliases and visible native namespace mutation or escape
do not establish native collection identity.

Reads, aliases, captures, unknown calls, deletes, compound writes, rebinding and
control flow end the proof. Values must be primitive and keys statically known.
A different key ends the preceding insertion proof because insertion order can
matter. RHS values must complete without coercive arithmetic: only literals,
negative numeric literals and stable initialized primitive reads qualify. Reads
must belong to the current execution owner and follow a declaration in an
enclosing ordinary statement list. An earlier switch case cannot establish
initialization in a later case, and a hoisted call cannot establish that an
outer binding was initialized. Array length writes, object prototype keys,
accessors, spreads and opaque storage are excluded. Visible direct/computed
global constructor paths, optional and chained global namespace access,
namespace aliases and prototype reflection invalidate native storage in typed
and untyped modes. Shadowed global names do not identify the native namespace.
Unknown facts are never restored from an old initializer.
Indirect external monkey-patching is not inferred. These conservative boundaries
are disclosed blind spots for corpus review, not proof that all overwrites are
detected.

## no-identical-expressions

Reports equal stable primitive operands for subtraction, division, remainder,
equality, relational, bitwise and logical operations. It retains operators,
literal values, regex flags and binding identity. Primitive types are obtained
from the real type checker when available; otherwise only stable literal-based
bindings qualify. Calls, getters, member access, updates and object coercion are
excluded. Self-inequality NaN idioms and useful equal-operand shifts, addition,
multiplication and exponentiation are not reported by this rule. Other existing
rules continue to own their checks of such expressions.

## no-identical-functions

Reports equal function implementations with the same runtime form and parameter
contracts. Local variables may be renamed only within corresponding scopes;
different free bindings, literals, async/generator forms and receiver homes stay
distinct. `new.target` is excluded. The default minimum is three nonempty code
lines in the body, with an integer option of at least three. Comments and blank
lines do not satisfy the threshold.

## no-redundant-jump

Reports a jump only when ordinary completion reaches the same destination.
Loops, labels, switch cases and finalizers participate in that proof. A lone
jump in a block, switch break/return and labeled continue are explicit documented
abstentions. Necessary early exits and effects after a jump remain distinct.

## no-use-of-empty-return-value

Reports assignment, argument and other value uses of a proven void result. Type
services establish callable contracts, including contextual void callbacks. The
untyped fallback follows stable direct local function bindings and bodies with
no value return. Unknown calls, explicit undefined values, mixed value unions,
async promises, generators and never-returning bodies are not assumed empty.
Awaited void is checked separately from a useful Promise of void. Effect-only
expression statements, void discards, discarded sequence/conditional/logical
tails and return delegation are retained.

## prefer-immediate-return

Requires real type services. Reports a sole const initializer followed
immediately by return or throw when the temporary has exactly that value read.
Annotations, casts, contextual or overloaded return contracts, additional uses,
captures, finalizers and unresolved types abstain. Callable and constructable
temporaries are retained because named evaluation can supply a function or class
name; inlining must not silently erase that value. Initializer and local types
must be mutually assignable. The temporary's type or lifetime must not carry
an additional contract.

## Validation

The ordinary `scripts/*.test.ts` Node entry uses the MIT RuleTester adapter,
explicit valid/invalid cases, exact diagnostic messages and no-fix assertions.
Typed cases write their actual source to private fixture files and use an
explicit tsconfig. They do not use a broad default-project glob. The existing
10,000 ms Node limit and Vitest worker limits remain unchanged.

The 32 historical independent challenges are retained verbatim as case sources,
with explicit independently selected expectations. They are requirements for
the owned rules, not observed results of a third-party implementation. Additional
adversarial cases cover fallthrough, receiver and binding identity, primitive
coercion, collection escape, meaningful promises and type-context changes.
