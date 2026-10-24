# Code review rules

The checklist a reviewer applies to a diff. Each rule is one principle,
what to flag, and what is fine. It holds in any codebase and any company:
no rule names a company, team, product, internal system, business domain
object, person, ticket or merge request. The long form of every rule, with
all the shapes it has been seen in and an example, is in
`mr-feedback-reference.md`; the two files share their slugs.

Format, which review tooling parses: a `## <category>` heading per section
and, per rule, a `### <category>/<slug>` heading, one principle paragraph,
a `Flag:` line and a `Fine:` line. Slugs are stable once published.

## types

### types/invalid-states-unrepresentable
Closed sets, required parts, units and mutually exclusive alternatives are
types, not strings, booleans or optionals that code has to interpret.
Flag: a string or integer compared against fixed literals; an optional or
collection guarded at every access instead of required at construction; a
sentinel ("", 0, epoch) meaning absent; identity stored twice or compared by
one component.
Fine: free-form text; fields the domain genuinely makes optional; an opaque
identifier kept whole when it does not parse.

## structure

### structure/every-layer-earns-its-keep
A helper, wrapper or mirror type is justified by behaviour, an invariant or
a clearer name. Otherwise inline it.
Flag: a one-expression function with a longer name than its body; a
pass-through function; a struct mirroring another field for field, or
grouping one owner's fields under a name that means nothing on its own.
Fine: a helper that enforces an invariant or gives a meaningful name to
something called from many places.

### structure/find-the-existing-mechanism
If the codebase, a dependency, a derive or generated code already does it,
call it.
Flag: the same match repeated at call sites that could be one method on the
owning type; a hand-written mapping where a derive exists; a single-item
path duplicating the bulk path; a parallel type, helper, module-per-variant
or hand-rolled loop beside an existing mechanism that already fits.
Fine: the existing mechanism has different semantics and would need flags
to fit, and the change says so with a requirement the task stated, not one
invented to defend the custom code.

### structure/behaviour-where-every-caller-gets-it
A policy that applies to every caller lives on the shared path. If the
second consumer would have to copy it, it is in the wrong place.
Flag: a caller filtering or refetching because the shared query lacks the
criterion; the same guard or timeout repeated in several handlers; an
enrichment every consumer needs computed in one of them.
Fine: a precondition only one caller needs.

### structure/facades-are-complete-and-backend-agnostic
A facade over interchangeable backends supports every operation for every
backend and exposes nothing only one backend can do. Proxies forward; they
do not choose.
Flag: a facade method returning "not supported" for one backend; a helper
on the facade usable by one backend; a proxy selecting between targets in
its handlers.
Fine: backend-specific mechanics inside that backend's module.

### structure/modules-own-their-concept
Code lives with the concept it is about: generic utilities in generic
modules, domain types in the domain's schema, constructors on their type,
operations on a value's internals inside the value.
Flag: a generic utility declared in the domain module that first needed it;
a free function building the adjacent type; a caller dismantling a value's
internals to rebuild a variant of it; one sub-concern's types, selection
logic and tests accreting in a shared file.
Fine: cross-type orchestration; a small cohesive inline module.

### structure/build-once-separate-per-call-state
Long-lived handles are built once and reused; per-call state travels with
the call, never on the shared handle.
Flag: a router, client or registry rebuilt per evaluation; mutable per-call
flags on a shared component.
Fine: a handle whose contract requires per-call construction, done
explicitly and cheaply.

## data-flow

### data-flow/never-collect-a-stream-to-process-it
Streamed or paginated data is requested as narrowly as the source can serve
and processed per item, with independent remote work run under the
configured concurrency bound. Collecting first makes memory unbounded and
hides it behind a function still called "stream".
Flag: `.collect()` on a production stream followed by a loop, or a slice
parameter downstream of a stream; remote calls awaited one at a time, or
buffered to `usize::MAX` or a literal, instead of under the configured
bound; a sweep reading every row where a key-range predicate would exclude
most at the source; a manual limit where `take` would do.
Fine: tests; a stage that needs the whole set, with the bound stated;
dependent or lock-serialised calls.

### data-flow/a-parse-is-not-a-resolution
Parsing a reference proves its shape, not that its target exists. It is a
lookup key until the lookup confirms it.
Flag: a parsed reference used directly as identity; a fallback labelling any
nonempty reference as a kind without decoding it as that kind.
Fine: using the parse result to select which lookup to attempt.

## control-flow

### control-flow/loops-are-bounded-and-guarded-first
Polling, retry and pointer-following loops have an explicit finite bound,
checked as the first statement so no branch skips it, and bound by elapsed
time with capped backoff rather than iteration count. A resumable job ends
explicitly when its cursor reaches the end; it does not wrap around.
Flag: a timeout check inside one match arm; a fixed interval with an attempt
cap standing in for a deadline; a `while` over externally supplied pointers
with no depth limit; a sweep that clears its cursor on completion and
starts over.
Fine: a per-branch check in addition to the top guard; a loop over a finite
collection; a job meant to run continuously over a changing set.

## errors

### errors/unexpected-failures-block
A programming error or malformed data is not "nothing to do". It stays
visible and blocks until investigated.
Flag: a malformed-input branch sharing the ignore or skip disposition with a
genuine empty result; an error swallowed to keep a pipeline moving; a
per-item tolerant decode path for a failure the invariants rule out.
Fine: a terminal no-work outcome the design explicitly allows; untrusted
external input or a failure that genuinely occurs in production data.

### errors/errors-stay-typed-and-keep-their-cause
Known failure modes are an enum, not strings or a boxed `anyhow`. Context
wraps the original error; it never replaces it.
Flag: `map_err(|_| Generic)` discarding the cause; downcasting to tell
failure modes apart; a boolean followed by a hand-written reason string.
Fine: an outermost diagnostic wrapper that still carries the chain.

### errors/no-panics-in-production-paths
Production state is constructed so invariants hold by type. `expect`,
`unwrap` and `panic!` in production need an explicit recorded decision.
Flag: a check followed later by `.expect("checked above")` on the same data;
a "must exist" lookup unwrapped.
Fine: tests; catching an unwind at an integration boundary.

## observability

### observability/log-through-the-redacting-facade
Everything that reaches a log goes through the logging facade and its typed,
redacting fields. Bypasses are for emergencies. Trace context uses the
installed propagator.
Flag: direct `tracing::warn!` or `println!` where a facade exists; a value
wrapped in a plaintext escape hatch; a hand-assembled `traceparent`.
Fine: the facade's own implementation.

### observability/one-log-per-fact-at-its-owner
Each fact is logged once, by its owner, with its origin. Callers count;
aggregators emit their own summaries.
Flag: a caller re-logging a failure the callee logged; a shared handler that
cannot say which trigger started the work; callers unpacking a tally to log
its fields; the same error rendered into two fields of one line.
Fine: a caller-level summary over many outcomes; a short rendering stored
in a bounded status field.

### observability/report-the-summary-point-to-the-detail
A reporting command prints a compact summary and the command that shows
the full detail where it lives; it does not dump the detail.
Flag: a complete diff or other large body written to standard output by a
comparison command; a result type carrying summary and full text only to
print both; tests asserting on the full text instead of the summary.
Fine: a tool whose purpose is emitting the full text for piping; output
small enough that a summary hides everything useful.

## naming

### naming/names-track-concepts
A name says what the thing is at its container's level of abstraction;
distinct concepts keep distinct names.
Flag: an enriched type renamed to its raw type's name; `<P, O, E>` where
role names would read; sibling variants at different levels of abstraction.
Fine: conventional single-letter parameters where there is one.

## docs

### docs/describe-the-present-accurately
Comments and docs say how the code works now and name the actual systems it
talks to. History goes in version control unless it explains a live
constraint; a measured claim carries evidence for the paths it was not
meant to change as well.
Flag: a comment narrating the old bug before the current behaviour; a
generic service label where the concrete one is known; a universal claim
with an unstated exception, or a speed-up claim with no evidence for its
untouched neighbours; prose about cases no code implements.
Fine: history that is the reason for a current constraint.

### docs/instructions-state-the-observable-contract
Instructions for an interface's consumer, human or LLM agent, state what
it can observe and must do, as positive directives; not how the thing
behind the interface works, and not what not to expect.
Flag: a prompt or guide narrating a wrapper's internals; a checklist item
verifying internals the reader cannot see; runs of "do not expect / do not
select / does not block" where one positive rule would do.
Fine: the result shape and the failure outcome to select; a single named
prohibition at a safety boundary.

## tests

### tests/tests-do-not-shape-production-code
Production APIs, concurrency and safety guards follow production needs. A
test that cannot pass without changing them is evidence about the harness.
Flag: an injection parameter only tests supply; a production lock justified
by test timing alone; heartbeat output added to silence a stall guard.
Fine: a production defect a test found.

### tests/separate-harness-setup-execution-assertion
Test support lives in its own module; test bodies read as setup, execution,
assertion, with each case and input named; tests run in the language's test
framework.
Flag: a test module made `pub(crate)` for sharing; one file mixing harness
and scenarios; repeated large setup blocks or positional tuple tables that
hide what each case varies; a wrapper script invoking one test.
Fine: small local tests; scenario-specific assertions kept with their
scenario.

### tests/property-tests-generate-structure
Strategies compose over the case type's fields so shrinking preserves
structure; seeds are scalars; stateful APIs are tested with action
sequences against an independent model.
Flag: raw bytes decoded into a case; a hand-written seed array; a stateful
API tested only with isolated calls; a model that echoes the implementation.
Fine: inputs whose real contract is raw bytes; protocol vectors.

### tests/fakes-mirror-the-real-contract
A stub, fixture or corpus enforces the real contract's constraints and is
extended through its own mechanism.
Flag: a stub accepting any string for an enum field; constants compared
against other constants instead of the real schema; a stub or corpus
extended through code (branches, request sniffing, generators) instead of
its own data.
Fine: a field the real contract leaves free-form; a generic harness
capability every scenario can use.

### tests/snapshots-and-steps-assert-behaviour
Snapshots assert full deterministic output with variability removed at the
fixture, and reusable steps carry no single scenario's expectations. Moving
storage or enforcement does not change what is asserted.
Flag: redaction added where the fixture could be fixed; partial output
instead of an error for missing required structure; a step asserting one
scenario's literals; an artifact asserted by name alone, or expectations
flipped in a behaviour-preserving migration.
Fine: genuinely nondeterministic external data; a requested behaviour
change; content a later step already snapshot-compares.

### tests/every-test-earns-its-keep
A test is justified by the behaviour only it pins, a harness by the tests
that need it, a tag by the selector that reads it. Plumbing restatements
and proofs of a removal the updated suite already proves are deleted.
Flag: a test asserting config defaults load or a mock returns what it was
given; a new test file or fake server added to prove a removal while the
existing tests are updated to the same shape; a tag nothing selects.
Fine: a behavioural invariant no higher-level suite observes cheaply; a
test for behaviour the change introduces; a tag a filter or convention
consumes.

## scope

### scope/a-fix-touches-the-failing-decision-and-nothing-else
A bug fix changes the wrong decision point. New types and cross-layer
rewrites need a requirement, not a bug. A tool does its stated job and no
more.
Flag: a narrow fix arriving with new model types and factories across
unrelated layers; a task that produces an artifact also performing its side
effects.
Fine: several files changed because the decision genuinely spans them.

### scope/new-policy-is-prospective-and-deferrals-stand
New policy applies from now on; history is not backfilled without a
requirement. A deferred consumer keeps its compatibility API with the
removal condition written down, and a subsystem being retired gains no new
capability on the way out.
Flag: a job rewriting rows that predate the policy; a migration also
rewriting a deferred consumer; new functions, types or tests added to a
module the same series of changes is deleting.
Fine: a documented backfill requirement; a consumer with no deferral; a
minimal fix the migration needs to run.

### scope/no-speculative-surface
A job, type or configuration carries only what a present caller needs;
unrequested modes, parameters and override layers are deleted, not
defaulted, and a cross-cutting setting has one configuration point.
Flag: a parameters type with several optional fields plus a validator of
their combinations for a single-purpose job; a dry-run/apply switch nobody
asked for; a per-entity override map layered over a global setting with no
entity that differs.
Fine: a parameter the production caller supplies; state the job writes for
itself; an override with a named, demonstrated need.

### scope/experiment-scaffolding-does-not-ship
What was added to measure, trial or debug a change is removed, or replaced
by its stable form, before merge; the measurements live in the write-up.
Flag: timing or profiling flags, retained timing artifacts and tool-version
prints beside the functional change; a cache key, namespace or path
carrying an issue number, trial label or experimental commit hash.
Fine: diagnostics operators rely on every run; deliberately versioned or
environment-scoped identifiers described in the operations docs.

## config

### config/effective-settings-are-visible
The value a system runs with is declared where operators look, with
overrides merging over a visible default. Shared dependency versions are
pinned once at the workspace root.
Flag: a setting silently taking a compiled default the configuration omits;
a code path forcing on a behaviour an operator switch already governs; a
crate pinning a version the workspace already pins.
Fine: a deliberately optional override; test construction with a fixed
default; a feature reading the existing switch.

### config/removal-is-traced-and-verified
Removing an exposed surface removes everything that reaches it transitively,
then verifies at the boundary where exposure happens.
Flag: siblings whose request or response types still reach the removed type;
an annotation deleted without checking the gateway stopped registering it.
Fine: operations outside the dependency closure.
