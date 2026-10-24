# Code review rules: reference

The long form of the review rules. Each rule here is a principle a reviewer
applies to a diff, written so that it holds in any codebase and any company,
with the full list of shapes it has been seen in and a synthetic example. The
compact form that review tooling actually consults is `mr-feedback-rules.md`;
the two files share their slugs, and every rule exists in both.

This document exists for people and for the process that folds new review
feedback into the rules: it carries enough context to decide whether a new
observation is an instance of an existing principle. Contributions are
welcome under these constraints:

- **Generic.** A rule names a code shape, a design principle or a public
  technology. It never names a company, team, product, internal system,
  business domain object, person, ticket or merge request.
- **One principle per rule.** A new observation that fits an existing rule
  adds a shape to its "Looks like" list rather than a new rule.
- **Recognisable in a diff.** "Looks like" lists the structural patterns a
  reviewer can see in changed lines. "Not this" names the superficially
  similar cases that are fine.
- **Examples are synthetic.** Example code is written for the rule, in a
  public language or library, and carries no real identifiers.

Format: a `## <category>` heading per section, a `### <category>/<slug>`
heading per rule, one principle paragraph, a `**Looks like:**` list, a
`**Not this:**` paragraph, and an optional `**Example (<language>):**`
block. Slugs are stable once published.

## types

### types/invalid-states-unrepresentable

If a value has a closed set of meanings, a required part, a unit or mutually
exclusive alternatives, the type says so. A string, a bare integer, a boolean
or an optional that surrounding code has to interpret is a convention, and
the compiler does not check conventions.

**Looks like:**
- A string compared against, or built from, a fixed set of literals.
- A boolean where callers need to know *which* case, and reconstruct a
  reason or label from the flag later.
- Two or more separately stored settings that are really one choice between
  alternatives, each with its own payload.
- An optional, or a collection, that the code only works with when present
  or nonempty, guarded at every access instead of required at construction.
- Identity or a timestamp recomputed or defaulted ("", 0, epoch) inside a
  getter rather than established once in the constructor.
- Two fields carrying the same identity in different representations, such
  as a string id beside a typed reference.
- Timestamps, identifiers, amounts or file handles as plain strings or
  integers in API, storage or CLI models; CLI values validated against a
  hand-written list instead of derived from the enum.
- Identity compared by one component or by its string rendering when the
  type has a full structural identity.
- An empty string, zero or a magic number standing in for "absent".
- A convenience accessor that returns one enum variant and silently falls
  back on the others, hiding which variants a call site accepts.
- Structured input parsed with a hand-rolled delimiter grammar or by walking
  untyped JSON, where a typed model and a deserialiser would express it.
- A closed set of test shapes encoded as integers, or an enum's variants
  listed by hand in an array instead of iterated.

**Not this:** free-form diagnostic text, genuinely open-ended values, fields
the domain really does make optional, and an identifier whose contract is to
be preserved opaquely when it does not parse (keep the full string, and
represent an absent component as `None`, distinct from zero).

**Example (Rust):**
```rust
// Before: any string is a reason, and callers spell it differently.
struct Job { rejected: Option<String> }

// After: the set is closed and serialisation is unchanged.
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum RejectReason { Expired, Duplicate, TooLarge }
struct Job { rejected: Option<RejectReason> }
```

## structure

### structure/every-layer-earns-its-keep

A helper, wrapper, mirror type or state machine is justified by one of three
things: behaviour, an enforced invariant, or a name clearer than its body.
If none applies, inline it.

**Looks like:**
- A function whose body is one expression and whose name is longer or vaguer
  than that expression.
- A function that only forwards its arguments to another function, often
  justified "for testability".
- A second struct mirroring the first field for field, plus copying
  conversion methods, with no distinct validation or shape, added only to
  derive serialisation.
- A hand-rolled lifecycle (open/frozen/poisoned flags, repeatable finish)
  where consuming the value into its next form would do.
- A struct grouping two or three fields that only ever travel together
  inside one owner, named for nothing on its own, existing to host a
  constructor or a pairing check that belongs on the owner.

**Not this:** a helper that enforces an invariant, hides a genuinely
different representation, or gives a meaningful name to something called
from many places.

**Example (Rust):**
```rust
// Before
fn is_oversized(payload: &[u8]) -> bool { payload.len() > MAX_LEN }
if is_oversized(&payload) { ... }

// After
if payload.len() > MAX_LEN { ... }
```

### structure/find-the-existing-mechanism

When the codebase, a dependency, a derive macro or generated code already
does the thing, call it. A hand-rolled copy drifts from the original and
doubles the review surface.

**Looks like:**
- The same match or branch repeated at several call sites that could be one
  method on the type owning the data.
- A predicate about a type's internals (is it empty, is it valid) written at
  a serialisation or filtering boundary instead of on the type.
- A companion enum plus a hand-written mapping where a derive exists.
- A new string conversion or custom serialiser for a type that already has
  parse and display implementations.
- A manual conversion from a generated protocol field where the generator
  already emits an accessor (keep the validation the accessor skips).
- A single-item code path written beside the bulk path instead of calling
  the bulk path with one item.
- A caller that branches on one variant of a reference and discards the
  others before delegating to a resolver that handles all of them.
- A feature-specific type, helper or test step declared beside an existing
  generic one that already fits: a second configuration struct, a parallel
  lookup step, a bespoke per-call scope.
- A new instance of an enumerated concept built as its own module and
  wrapper enum, with a fallible conversion whose only error is the odd one
  out, instead of as a variant beside its siblings.
- A hand-rolled retry, backoff, rate-limit or cache loop with its own policy
  struct, delay arithmetic and error enum, where a dependency the workspace
  already has provides the primitive.

**Not this:** the existing mechanism has different semantics and would need
flags to fit. Then a new one is right, and the change should say why, citing
a requirement the task actually stated rather than one invented to defend
the custom code.

**Example (Rust):**
```rust
// Before: a parallel enum and a hand-maintained match.
enum FilterKind { ByName, ByDate }
impl Filter { fn kind(&self) -> FilterKind { match self { ... } } }

// After
#[derive(strum::EnumDiscriminants)]
enum Filter { ByName(String), ByDate(Range) }
```

### structure/behaviour-where-every-caller-gets-it

A policy that applies to every caller lives on the shared path, not in each
caller. If the second consumer would have to copy it, it is in the wrong
place.

**Looks like:**
- A caller fetches a broad result and filters or refetches in application
  code because the shared query lacks the criterion.
- The same eligibility, retry-cap or timeout guard repeated in several
  handlers while their common entry point has none.
- Callers allocate reply channels, await responses and record latency around
  a submission API that could own that round trip.
- Each backend fans a shared failure out to its pending requests instead of
  returning one error to the generic layer that holds the requests.
- Consumers convert raw wire rows to domain values after receiving them from
  a channel that could deliver domain values.
- An enrichment (pricing, resolution, classification) computed in one
  consumer when every consumer of the stream needs it.
- A shared destination or upload path resolved anew in each producer.

**Not this:** a precondition or computation that only one caller needs.

### structure/facades-are-complete-and-backend-agnostic

An interchangeable facade over several backends exposes only operations
every backend supports, and supports each one for every backend. Transport
components forward; they do not choose.

**Looks like:**
- A new public facade operation that returns "not supported" for one of the
  backends it fronts. A no-op or synthetic result is the right answer for a
  null backend.
- A helper on the facade that can only work for one backend (its
  configuration, its traversal, its credentials).
- A forwarding proxy that stores a default target plus an alternative and
  selects between them inside request handlers.
- A proxy's request type declared in, and exported from, one backend's
  module.

**Not this:** backend-specific mechanics placed inside that backend's own
module.

### structure/modules-own-their-concept

Code lives with the concept it is about: generic utilities in a generic
module, domain types in the domain's module, constructors on the type they
build, and operations on a value's internals inside the value's type.

**Looks like:**
- A generic utility (a counter, a tally, a retry helper) declared inside a
  domain module because that is where it was first needed.
- A domain type declared in an operation or transport schema instead of the
  domain's own schema, and then duplicated.
- Shared dispatch code that grows domain-specific evaluation bodies, or a
  "facts" module that also maps results.
- A free function that constructs the adjacent type from its required inputs
  instead of an associated constructor.
- A caller that dismantles a value's internals to rebuild a variant of it
  (split, retry, resubmit) instead of asking the value to do it.
- An inline module that occupies essentially the whole file, costing an
  indentation level for nothing.
- The types of one sub-concern, the logic that chooses among them and their
  tests accreting in a file whose other contents are shared logic, instead
  of in a child module the parent calls once.

**Not this:** cross-type orchestration, or a small cohesive inline module.

### structure/build-once-separate-per-call-state

Long-lived handles (routers, clients, compiled matchers, pools) are built
once at initialisation and reused. Per-invocation state is passed through
the call, never stored on the shared handle.

**Looks like:**
- A router, client or registry rebuilt on every evaluation.
- Mutable per-call flags or context stored on a shared, reused component.

**Not this:** a handle whose contract genuinely requires new per-call state,
where the construction is cheap and explicit.

## data-flow

### data-flow/never-collect-a-stream-to-process-it

Data that arrives paginated or streamed is requested from its source as
narrowly as the source can serve, then enriched, filtered and written per
item as it arrives, with independent per-item remote work run under a
configured concurrency bound. Collecting into a vector first turns bounded
memory into unbounded memory and hides that behind a function still named
"stream"; awaiting remote calls one at a time turns bounded latency into a
serial sum; sweeping every row to discard most of them moves the store's
selection into the process.

**Looks like:**
- Collecting a production stream into a vector, then looping over the result.
- A function taking a slice that sits downstream of a stream.
- A manual drain loop whose only work is pushing items into output buffers,
  where a fold carries the buffers as state.
- A manual counter and break where `take` on the stream does the same, or a
  producer count parameter that unlimited callers satisfy with `usize::MAX`.
- A concurrency buffer sized `u32::MAX`, `usize::MAX` or a literal instead
  of the configured bound.
- A dedup or scan pass run repeatedly over the same accumulated rows
  (`filter(p).count()` followed by `find(p)`); deduplicate before submission
  and look each result up once.
- A loop awaiting independent remote lookups one at a time, where a stream
  over the items with an order-preserving bounded buffer would run them
  concurrently.
- A sweep or paging query that reads every row and discards most of them in
  application code, where a key-range predicate the index serves would
  exclude them at the source; the ranges derived from the type that defines
  them, not hard-coded.

**Not this:** test code that collects to assert; a stage that needs the
whole set (sort, global dedup), where the collection point is explicit and
bounded; calls that depend on each other's results or must be serialised
against a shared lock or rate limit; a filter on an unindexed column that
would not narrow the read.

### data-flow/a-parse-is-not-a-resolution

Successfully parsing a reference proves its shape, not that the thing it
refers to exists. Treat a parsed reference as a lookup key until the lookup
confirms it, and derive identity from the confirmed source.

**Looks like:**
- A parsed or decoded reference used directly as deduplication identity.
- A fallback branch that labels any nonempty reference as a particular kind
  and issues that kind's query without decoding it as that kind first.

**Not this:** using the parse result to *select* which lookup to attempt.

## control-flow

### control-flow/loops-are-bounded-and-guarded-first

Every loop that follows pointers, polls or retries has an explicit finite
bound, and its exit condition is the first statement in the body so no
branch can skip it. Bound polling by elapsed time with capped backoff, never
by iteration count alone. A resumable job is a loop across runs: once its
cursor reaches the end it stays there and the next run ends explicitly,
rather than clearing the cursor and starting over.

**Looks like:**
- A timeout check inside one match arm or branch of a loop.
- A polling loop with a fixed short interval and an attempt cap standing in
  for a deadline.
- A `while` that follows `source()` or `next` pointers of externally supplied
  data with no depth limit or cycle check.
- A resumable sweep that resets its cursor to the start once everything is
  traversed (`cursor = if complete { None } else { last }`), instead of
  keeping it at the end and reporting completion on the next run.

**Not this:** a per-branch check that exists in addition to the top guard;
a loop over an independently finite collection; a job designed to run
continuously over a changing set, where starting over is the intended
behaviour.

**Example (Rust):**
```rust
let started = Instant::now();
let mut delay = Duration::from_millis(500);
loop {
    if started.elapsed() > DEADLINE { return Err(Timeout) }
    match poll().await? {
        Ready(v) => return Ok(v),
        Pending => { sleep(delay).await; delay = (delay * 2).min(MAX_DELAY); }
    }
}
```

## errors

### errors/unexpected-failures-block

A programming error or malformed data is not a "nothing to do" outcome.
It stays visible and blocks until someone investigates. Only a known,
legitimate no-work result may be classified as ignorable.

**Looks like:**
- A malformed-input or invariant-violation branch sharing the "ignore" or
  "skip" disposition with a genuine empty result.
- An error swallowed to keep a pipeline moving.
- A per-item tolerant path (a raw record type that leaves columns undecoded,
  a per-item `Result` inside a page, an "undecodable" report entry) for a
  failure the system's invariants say cannot happen.

**Not this:** a terminal no-work outcome the design explicitly allows;
validation of untrusted external input, or a failure mode that genuinely
occurs in production data.

### errors/errors-stay-typed-and-keep-their-cause

Known failure modes are an enum, not a string or a boxed `anyhow`. Adding
context to an error wraps the original; it never replaces it.

**Looks like:**
- `map_err(|_| SomeGenericError)` discarding the cause.
- A shared `Arc<anyhow::Error>` and downcasting to tell failure modes apart.
- A boolean "is terminal" followed by a hand-written reason string.

**Not this:** an outermost diagnostic wrapper at a process boundary that
still carries the chain.

### errors/no-panics-in-production-paths

Production code constructs its state so that invariants hold by type, and
never reasserts them with `expect`, `unwrap` or `panic!`. An intentional
production panic needs an explicit, recorded decision.

**Looks like:**
- An emptiness or membership check followed later by `.expect("checked
  above")` on the same data; accept the nonempty type at the boundary instead.
- A lookup of a value that "must" exist, asserted with `unwrap`.

**Not this:** test code, and catching an unwind at an integration boundary.

## observability

### observability/log-through-the-redacting-facade

Everything that reaches a log goes through the project's logging facade and
its typed, redacting field implementations. Bypasses exist for emergencies,
not for convenience. Cross-cutting context such as trace propagation uses
the installed propagator, never hand-assembled headers.

**Looks like:**
- Direct `tracing::warn!` or `println!` in application code that has a
  logging facade.
- A domain value wrapped in a "plaintext" or "unredacted" escape hatch
  because the typed implementation was not written.
- A second error-chain renderer beside the existing one.
- A `traceparent` header assembled with a format string.

**Not this:** the facade's own implementation.

### observability/one-log-per-fact-at-its-owner

Each fact is logged once, by the component that owns it, with the context
needed to understand it. Callers count; they do not re-log what the callee
already logged. Aggregators emit their own summaries.

**Looks like:**
- A caller emitting a per-item error event for a failure the callee already
  logged.
- A shared handler reached from several triggers that cannot say which
  trigger started the work; pass a typed origin.
- Callers unpacking a tally's internals to emit the same summary fields in
  each place.
- One failure rendered into two fields of the same log line, a short form
  beside the full chain; one field carrying the full chain is the log, and
  the short form belongs only where a bounded value is stored.

**Not this:** a caller-level summary over many outcomes; a short rendering
written to a persisted status field with a length limit.

### observability/report-the-summary-point-to-the-detail

A command whose job is to report a comparison or a result prints a compact
summary and a ready-to-paste command for inspecting the full detail where it
already lives; it does not dump the detail to the terminal. The detail has
an owner (a repository, a file, a store) and the report names it.

**Looks like:**
- Standard-output writes of a complete diff or other large body from a
  command whose job is to report the result of a comparison.
- A result type carrying both a summary and the full text only so the entry
  point can print both.
- Tests asserting on the full-text field rather than on the summary or the
  exit code.

**Not this:** a tool whose explicit purpose is to emit the full text for
piping (a `--full` or patch-format flag); output small enough that a summary
would hide everything useful.

**Example (Rust):**
```rust
// Before: the whole diff scrolls past.
println!("{}", result.stat);
println!("{}", result.full_diff);

// After: the summary, and where the detail lives.
println!("{}", result.stat);
println!("Full diff:  git -C {dir} diff");
```

## naming

### naming/names-track-concepts

A name says what the thing is at the conceptual level of its container.
Distinct concepts keep distinct names, generic parameters are named by role,
and sibling variants are named at the same level of abstraction.

**Looks like:**
- A rename that gives a derived or enriched type the name of the raw type it
  wraps.
- `<P, O, E>` where `Payload, Output, Error` would read.
- An enum of domain entities with one variant named after a tool or format
  one of those entities happens to use.

**Not this:** conventional single-letter parameters where there is one.

## docs

### docs/describe-the-present-accurately

Comments and documentation explain how the code works now and name the
actual systems it talks to. History belongs in version control unless it
explains a live constraint. Hypothetical behaviour is not documented. A
claim about measured behaviour carries the evidence that establishes it,
including for the paths the change was not meant to affect.

**Looks like:**
- A comment recounting the old bug or the removed implementation before
  explaining the current one.
- A data-source table with an inferred or generic service label where the
  concrete owner and endpoint are known.
- A universal claim ("every row has a public id") with an unstated exception.
- Contract prose about cases no code implements.
- A performance or behaviour write-up whose before/after evidence covers
  only the targeted path, while siblings that share its configuration have
  no row at all.

**Not this:** a sentence of history that is the reason for a current
constraint.

### docs/instructions-state-the-observable-contract

Instructions written for the consumer of an interface, whether a person, a
client or an LLM agent, state what the consumer can observe and must do: the
inputs, the shape of the result and the decision rule, as positive
directives. They do not narrate how the thing behind the interface is
implemented, ask the consumer to verify internals it cannot see, or define
behaviour by listing what not to expect, what not to select or what does not
block; consecutive negations are replaced by the single positive rule they
imply.

**Looks like:**
- A skill, prompt or usage paragraph beginning "The tool's wrapper gathers
  ... checks ... isolates ...", or a checklist item asking the reader to
  confirm that a wrapper validated an identifier.
- Runs of sentences of the form "Do not expect ...", "Do not select ...",
  "... does not block ...", "... comes from A, not B or C" in a prompt, skill
  or guide.

**Not this:** the result contract the consumer does see (the header a
response starts with, the shape of its body) and the outcome to select when
the call fails; a single explicit prohibition guarding a safety boundary,
where the forbidden action itself must be named.

## tests

### tests/tests-do-not-shape-production-code

Production APIs, concurrency and safety guards are shaped by production
needs. A test that cannot pass without changing them is evidence about the
test harness first.

**Looks like:**
- A production function gaining an injection parameter (an index picker, a
  clock, a lock) that only tests supply; use a narrowly scoped seam.
- A production lock or ordering change justified only by local test timing,
  with no production scenario that reproduces it.
- Periodic heartbeat output added so a stall or liveness guard stops firing
  during legitimate waits; fix the guard's threshold instead.

**Not this:** an independently established production defect found by a
test.

### tests/separate-harness-setup-execution-assertion

Test support (clients, builders, strategies) lives in its own module; test
bodies read as setup, execution, assertion, with every case and input named.
Property and unit tests run in the language's test framework, not through a
wrapper script.

**Looks like:**
- A test module made `pub(crate)` so another module can import its helpers.
- One large file mixing harness machinery and scenario bodies.
- Tests repeating large undifferentiated blocks of model construction and
  wiring.
- A shell or Python script that inventories dependencies and invokes one
  test.
- A table-driven test over a slice of positional tuples, where the
  framework's named cases or a struct with named fields would say what each
  column is.

**Not this:** small cohesive local tests, and scenario-specific assertions
kept with their scenario.

### tests/property-tests-generate-structure

Property strategies compose over the fields of the case type so exploration
and shrinking preserve its structure. Seeds are scalars expanded by the RNG.
Stateful APIs are tested with generated action sequences checked against an
independent model.

**Looks like:**
- A strategy that yields raw bytes which a custom decoder turns into a case.
- A hand-written 32-byte seed array.
- A stateful API tested only with isolated happy-path calls despite
  interacting operations, time advances or result classes.
- A "model" that echoes implementation state instead of predicting it.

**Not this:** tests whose real input contract is raw bytes; protocol vectors
that require exact bytes.

**Example (Rust):**
```rust
fn case() -> impl Strategy<Value = Case> {
    (any::<u8>(), prop::collection::vec(item(), 0..8), boundary_amount())
        .prop_map(|(kind, items, amount)| Case { kind, items, amount })
}
```

### tests/fakes-mirror-the-real-contract

A stub, fixture or corpus stands in for the real thing, so it enforces the
real thing's constraints and is extended through its own mechanism.

**Looks like:**
- A stub accepting any string for a field the real API restricts to an enum.
- A test that compares one hard-coded list of constants with another instead
  of exercising the real schema.
- A corpus extended through an in-test generator and parallel expectation
  machinery instead of new rows.
- A shared fixture that chooses identities for many test files; share the
  parsing, let each file own its constants.
- A shared stub gaining per-scenario state, request sniffing or hand-built
  responses in code, where the fixture data that drives it would carry the
  scenario.

**Not this:** a field the real contract leaves free-form; a generic
capability added to the harness that every scenario can use.

### tests/snapshots-and-steps-assert-behaviour

Snapshots assert full deterministic output; variability is removed at the
fixture, not hidden by redaction. Reusable steps carry no single scenario's
expectations. Moving storage or enforcement does not change what is
asserted.

**Looks like:**
- Recursive key or substring redaction added to a snapshot whose inputs the
  fixture could fix.
- Partial output with invented columns when a required block is missing;
  that is an error.
- A reusable step body asserting one scenario's literal values.
- Expected outcomes flipped in a migration whose brief was to preserve
  behaviour.
- Content assertions replaced by location assertions when storage moved.
- Test-only setup achieved by mutating downstream artifacts instead of a
  default-off debug option at the decision point.
- Explanatory prose in a Gherkin file without `#`.
- An end-to-end step that passes as soon as an artifact's name appears in a
  listing, without reading the artifact for a marker every valid one
  carries.

**Not this:** genuinely nondeterministic external data; an explicitly
requested behaviour change; an artifact whose content a later step already
snapshot-compares.

### tests/every-test-earns-its-keep

Every element of a test suite is justified by what only it does: a test by
the behaviour it alone pins, a harness by the tests that need it, a tag by
the selector that reads it. A test that restates plumbing (fixture shape,
configuration defaults, mock pass-through), proves a removal the updated
existing suite already proves, or re-asserts what an end-to-end suite covers
is maintenance without information; delete it rather than keep it by
default.

**Looks like:**
- A test module beside a new configuration struct asserting that its
  defaults load and unknown keys fail.
- A test constructing a mock and asserting it returns the value it was
  given, or that two fixture directories hold the same number of items.
- A removal diff that also adds a new integration-test file, often with its
  own fake server under a shared test directory, whose assertions enumerate
  "exactly these calls remain" or "this key is absent", while the same diff
  updates the existing tests and snapshots to the new shape.
- A scenario tag or marker that appears nowhere else but a lint allow-list,
  with no runner filter, suite selector or job reading it.

**Not this:** a test pinning a behavioural invariant or regression the
higher-level suites cannot observe cheaply; a new test for behaviour the
change introduces; a tag an existing filter, selector or recipe consumes, or
one the project's conventions require on every scenario.

**Example (Rust):**
```rust
// Before: the test proves the fixture is a fixture.
#[test]
fn fixture_returns_configured_value() {
    let backend = Backend::Fixture("ok".into());
    assert_eq!(backend.call(), "ok");
}

// After: nothing. The end-to-end scenario that drives the real path is the
// coverage.
```

## scope

### scope/a-fix-touches-the-failing-decision-and-nothing-else

A bug fix changes the decision point that is wrong. New types, factories and
cross-layer rewrites need a requirement, not a bug. A tool with a stated
job does that job and no more.

**Looks like:**
- A narrow fix arriving with new model types, codecs and factories across
  layers unrelated to the failing decision.
- A helper or task that was asked to produce an artifact also performing the
  side effects that artifact describes.

**Not this:** several files changed because the failing decision genuinely
spans them.

### scope/new-policy-is-prospective-and-deferrals-stand

A new policy applies to what happens from now on. Historical rows are not
backfilled without a requirement. A consumer explicitly deferred from a
migration keeps its compatibility API, with the removal condition written
down. A subsystem being retired gains no new capability on the way out: the
retiring change routes through what exists and classifies the result.

**Looks like:**
- A periodic job scanning for rows that predate a policy and rewriting them.
- A migration also rewriting a consumer that was deferred for coverage or
  pending model changes.
- New functions, types, display implementations and tests added to a module
  that the same series of changes is deleting.

**Not this:** a documented backfill requirement; a consumer with no deferral;
a minimal fix in the retiring code that the migration needs in order to run
at all.

### scope/no-speculative-surface

A job, type or configuration carries only what a present caller needs.
Modes, scopes, budgets, identifier lists, per-entity override layers and the
validation of their combinations that nobody asked for are deleted, not
defaulted; a cross-cutting setting is configured in exactly one place until
a concrete entity needs a different value. State the job writes for itself,
such as a resumption cursor, is not an operator parameter.

**Looks like:**
- A parameters struct or enum variant with several optional or defaulted
  fields, a `validate_*` function rejecting combinations of them, a
  dry-run/apply switch and tests for that validation, all for a
  single-purpose job.
- A per-entity override map shadowing a global map of the same type, with
  `overridden_by`-style merge helpers and doc comments describing a fallback
  chain of two or more layers, with no entity that needs a different value.

**Not this:** a parameter the production caller already supplies; a
resumption token the job writes and reads itself; an override introduced for
a demonstrated need, with the entity and reason named; a setting that is
inherently per-entity and has no global meaning.

**Example (Rust):**
```rust
// Before: five knobs and a validator for a job with one purpose.
struct Params {
    apply: bool,
    scope: Scope,
    budget_secs: u32,
    ids: Option<Vec<Id>>,
    cursor: Option<Id>,
}

// After: only the state the job writes for itself.
struct Params {
    #[serde(default)]
    cursor: Option<Id>,
}
```

### scope/experiment-scaffolding-does-not-ship

What was added to measure, trial or debug a change is scaffolding, not the
change. Timing and profiling flags, retained diagnostic artifacts, prints of
runner capacity or tool versions, and identifiers that encode the experiment
(a namespace, cache key or storage prefix carrying an issue number, a trial
label or a hash of an experimental commit) are removed or replaced with
their stable, purpose-named form before merge, and the measurements live in
the write-up.

**Looks like:**
- A pipeline or build-configuration diff that, beside the functional change,
  adds a timing or profiling flag to build arguments, an artifacts block
  retaining timing reports, and before-step lines printing environment or
  tool versions, copy-pasted across several jobs.
- A script or configuration value of the shape
  `<project>/<issue>/<variant>/<label>-<short-hash>` carried unchanged from
  trial pipelines into the delivery commit.

**Not this:** diagnostics that serve ongoing operation, such as a
cache-statistics artifact operators read on every run or a version check
that gates correctness; identifiers deliberately versioned or
environment-scoped (a `/v1` suffix, separate trusted-branch and
review-branch namespaces) and described in the operations documentation; an
issue reference in a comment rather than in a live value.

## config

### config/effective-settings-are-visible

The value a system actually runs with is declared where operators look:
schema and configuration files, with per-target overrides merging over a
visible default. Shared dependency versions are declared once at the
workspace root.

**Looks like:**
- An operational setting that silently uses a compiled default while the
  declared configuration omits it.
- A resolver or module supplying a tuning value through an implicit default
  method.
- A workspace member declaring a literal version for a dependency the
  workspace already pins.
- A code path that forces a behaviour on for one feature, bypassing the
  operator-controlled switch that already governs it.

**Not this:** a deliberately optional override; test construction with a
fixed default; a feature wired to read the existing switch, or the switch
extended through its normal configuration surface.

### config/removal-is-traced-and-verified

Removing an exposed surface means removing everything that depends on it
transitively, then verifying at the boundary where exposure actually happens.

**Looks like:**
- An exposure change that removes the methods importing a type directly but
  leaves siblings whose request or response types reach it transitively.
- Deleting an annotation and assuming the gateway stopped registering the
  operation, without checking reflection or the deployed surface.

**Not this:** operations outside the dependency closure.
