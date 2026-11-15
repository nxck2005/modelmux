# Efficiency review

Reviewed commit: `a4e7a6e` ("Fix silent label truncation in task-state extraction").
Review date: 2026-11-15.

Scope: every tracked source, test, contract, config, and documentation file in
the repository. This is a review only; no production code was changed.

**Status note:** the later security remediation commit fixed C1 (the Router now
snapshots the configuration), C2 (`deadline_ms` is bounded), C3 (identifiers
and input records are bounded), and C4 (boundary booleans are strict). The
remaining findings, including the duplicate fallback gate and the state
extraction costs, are unchanged and still stand as written.

## How this was done

- Read all 4,877 lines of Python, plus `pyproject.toml`, `examples/router.toml`,
  the CI workflow, the fixtures, the docs, and the structure and key constraints
  of the three generated schemas.
- Ran the configured checks: `uv run pytest -q` (200 passed in 0.66 s),
  `uv run ruff format --check src tests`, `uv run ruff check src tests`,
  `uv run mypy` (clean).
- Ran `ruff` with broader rule sets (`PERF`, `SIM`, `C4`, `RUF`, `PLR`, `ARG`,
  `B`, `TRY`, etc.) purely as a diagnostic. The useful hit was `ARG002` (an
  unused router parameter); the rest were style/complexity warnings, an
  unsorted `__all__`, and one `PERF401` false positive (details in D6).
- Micro-benchmarked the state, policy, decision, and routing paths, and used
  targeted probes for the config-mutability, deadline-overflow, boolean
  coercion, economy-veto, no-op-switch, and invalid-current/judge-failure
  behaviours. Numbers below are medians of tight loops on this machine.
- Environment: Linux, CPython 3.12.13, pydantic 2.13.5, httpx 0.28.1. CI pins
  Python 3.11, so absolute numbers there will differ slightly.
- Limits: no live Jev call, control service, CLI, or harness integration was
  exercised, because those are not implemented in this commit. All measurements
  are of the offline core; the Jev findings are from reading the adapter and its
  mock tests, not from observing the live API.

## Verdict first

The routing hot path is **not** a performance problem. One complete route with
an offline judge costs about **16 µs** in-process; state preparation for a
1,000-message history costs about **1 ms**; a real Jev call is hundreds of
milliseconds and a generation call is seconds. Local CPU is therefore four to
six orders of magnitude below the work the router exists to control.

What is worth fixing is not raw speed:

1. A judge call that **cannot influence the decision** is still made when the
   current configuration is invalid, and a judge failure on that path can
   produce a *different* (and worse) outcome than a judge success. This is both
   wasted work (a paid call when Jev is used) and a reliability risk.
2. A nominally frozen configuration contains a **mutable profile dictionary**,
   while the router caches a digest computed at construction. Mutating the
   config silently desynchronises the digest from behaviour.
3. State preparation scales with the **entire supplied history** on every turn,
   and does avoidable work for records that are dropped. A long session turns
   this into quadratic work across turns (still small in absolute terms, but it
   is the only superlinear path in the core).
4. Router fallback logic is duplicated four times, and one parameter is dead.
   This is maintenance risk more than CPU cost.
5. Several docs and repo details are stale, which costs every future agent
   context and can mislead a user about what is implemented.

Severity labels used below:

- **Correctness/robustness** – can produce a wrong outcome or a runtime error.
- **Waste at scale** – grows with session/config size and is avoidable.
- **Micro** – measured, but too small to matter unless the router becomes a
  high-QPS shared service.
- **Hygiene** – docs, repo, tooling; no runtime effect.

### Key findings at a glance

| ID | Area | Severity | Finding |
| --- | --- | --- | --- |
| R1 | Router | Reliability/waste | Judge runs before the current route is validated; its success scores are discarded and a failure takes a different fallback. |
| R2 | Router | Waste | First-turn unsafe boundary calls the judge, then always stops. |
| R3 | Router | Waste (micro) | Compatibility/context checks run before the safe-boundary check that overrides them. |
| R4 | Router | Maintainability | The fallback safety gate is duplicated four times. |
| R5 | Router | Dead code | `_active_decision` takes an unused `reason` parameter. |
| R6 | Router | Micro | Output limit resolved twice per switch. |
| R7 | Router | Micro/scale | Linear availability scan (6 µs at 100 profiles) and per-check allocations. |
| C1 | Config | Correctness | `profiles` is a mutable dict under a frozen config while the Router caches its digest. |
| C2 | Config | Robustness | Unbounded `deadline_ms` raises `OverflowError` at route time. |
| C3 | Types | Robustness | Identifiers and input record text are unbounded. |
| C4 | Contract | Strictness | Plain bool fields accept `"yes"`/`1`. |
| S1 | State | Waste at scale | Extraction is O(supplied history) per turn (~1 ms at 1,000 messages). |
| S2 | State | Waste | `_select` computes labels for records it drops. |
| S6 | State | Waste at scale | Pydantic `MessageRecord`s cost 0.52 µs each (677 µs per 1,000). |
| S7 | Types | Waste | Byte-cap check serializes the whole state on every validation. |
| K1 | Schemas | Hygiene | Stale contract schemas are never removed and never detected. |
| D1–D3 | Docs | Hygiene | Stale example comment, stale tests README, 736-line append-only handoff. |

---

## 1. Router (`src/modelmux/router.py`)

### R1. Judge call before the current route is validated — wasted work *and* a divergent fallback (**Policy inconsistency + waste**)

**Where:** `Router._route` (`router.py:402-421`) always calls the judge for a
`new_user_turn`; `_active_decision` (`router.py:496-508`) only afterwards asks
`_current_valid`. If the current is invalid, `_recovery_decision` ignores the
candidate and the judge scores entirely; the scores end up in
`RouteDecision.observation` but are not used to route.

The failure path is worse. `_judge_failure_decision`
(`router.py:630-657`) does not perform the invalid-current recovery; it goes
straight to the configured `judgment_fallback`. So the same request can route
differently depending on whether the judge succeeded, even though the success
result is discarded:

| Same request: current `normal` unavailable, available `cheap`/`hard`, compatibility fallback `hard`, judgment fallback `normal` | Result |
| --- | --- |
| Judge succeeds | `switch` → `hard`, reason `compatibility_fallback` |
| Judge fails | `stop`, reason `no_compatible_profile` |

Both rows were reproduced with a probe. The judge fails → the session stops even
though a usable compatibility fallback exists; the judge succeeds → the session
recovers. When Jev is configured, the success path is also a paid network call
whose scores are thrown away.

The plan contains both rules and does not order them: “Invalid current config
uses compatibility handling” and, separately, “Jev failure: retain current
compatible config; if none, use configured judgment fallback.” The code applies
the first rule when the judge succeeds and the second when it fails, so the two
paths disagree about which recovery to use. That is the inconsistency; the
individual branches are each defensible in isolation.

**Why it matters:** this is the clearest place found where the core does work
that provably cannot change the outcome, and where a transient judge outage
changes scheduling policy. R2 below is the same theme on the first-turn path.

**Suggested direction (pick one policy and test it):**

- Validate `current` before the judge call. If it is present and invalid, skip
  the judge and call `_recovery_decision(request, state, None)` directly. The
  observation becomes `None`, which the contract already allows.
- Or keep the judge call for audit but make `_judge_failure_decision` use
  `_recovery_decision` when `current` is present and invalid, so success and
  failure agree.

The first option also removes a potentially expensive call from a recovery
path; the second preserves the "one judge call per turn" rule. Either needs a
behaviour test for the invalid-current plus judge-failure case, which is
currently pinned to the divergent behaviour by
`test_judge_failure_fallback_respects_bound_history`.

### R2. First-turn unsafe boundary still calls the judge (**Waste**)

**Where:** `router.py:402-421` judges before `_first_decision`
(`router.py:459-475`). If `current is None` and
`safe_to_apply_before_generation` is false, the action is always `stop`
(`UNSAFE_BOUNDARY` when a compatible candidate/fallback exists, otherwise
`NO_COMPATIBLE_PROFILE`); the judge scores cannot change the action.

A first-turn unsafe request therefore makes a judge call (a paid one with Jev)
and then stops without using the observation. Check the boundary before judging
when there is no current route, or document that the observation is deliberate
audit evidence.

An empty `available_profiles` set is the same shape: no candidate can be
eligible and the action is `stop` regardless of the scores. A cheap precondition
check (no current, unsafe boundary; or no availability at all) avoids the judge
call in both cases. This is a smaller win than R1 because it may be deliberate
audit evidence, but it is the same wasted-work pattern.

### R3. Compatibility work before the safe-boundary check (**Waste, micro**)

**Where:** `_first_decision` (`router.py:468-491`), `_recovery_decision`
(`router.py:575-590`), `_judge_failure_decision` (`router.py:639-651`).

In each case the code resolves the profile, availability, capabilities, effort,
output ceiling, and context fit, and only then checks
`safe_to_apply_before_generation`. When the boundary is unsafe the action is
always `stop` (the reason may vary), so the compatibility work is discarded.
Instrumentation confirms one full compatibility evaluation on the unsafe
first-turn path. `safe` is a request-level fact independent of the candidate;
check it first where the action does not depend on the candidate.

### R4. The fallback gate is duplicated four times (**Maintainability**)

**Where:**

- `_first_decision` fallback (`router.py:477-494`)
- `_active_decision` fallback (`router.py:543-565`)
- `_recovery_decision` fallback (`router.py:573-596`)
- `_judge_failure_decision` fallback (`router.py:637-657`)

`_recovery_decision` and `_judge_failure_decision` are identical apart from the
fallback id, reason, and observation; `_first_decision` is the same gate minus
the history check; the active-turn variant adds a rank check and `_switch_safe`.
Every new safety rule must currently be added in three or four places; the risk
of one copy drifting is real. A single helper such as

```python
def _checked_fallback(request, state, profile_id, reason, result, *,
                      require_escalation: bool, matched_rule_id=None)
```

would preserve the existing behaviour while giving the checks one home. This is
the one abstraction in this review that is clearly justified by repeated,
identical logic rather than by speculation.

A related duplication is the two “stop if unsafe, stop if history-incompatible”
branches in `_recovery_decision` and `_judge_failure_decision`; they are
identical apart from the fallback id, reason, and observation.

### R5. `_active_decision` takes an unused `reason` parameter (**Dead code, micro**)

**Where:** `router.py:501`. `_turn_decision` computes `reason` (line 428-438)
and passes it, but `_active_decision` never reads it; it chooses `ESCALATED`,
`RETAINED_CURRENT`, or `CANDIDATE_UNAVAILABLE` itself. `ruff --select ARG`
reports `ARG002` here. Remove the parameter and the corresponding argument, or
use it for the retained/escalated decision if the rule provenance is meant to
survive.

### R6. Output limit and compatibility resolved twice per switch (**Micro**)

**Where:** `_profile_compatible` computes `_candidate_output_limit` for its
checks (`router.py:686`), returns only the profile, and `_proposal` looks the
profile up again and recomputes `_candidate_output_limit` (`router.py:780-786`).
Instrumentation shows `_candidate_output_limit` called **twice per successful
switch**. A `(profile, output_limit)` return, or resolving the limit once in
`_turn_decision`, removes the duplicate lookup and guarantees the checked limit
and the proposed limit cannot diverge. The same applies to the profile
dictionary lookup itself.

### R7. Linear availability scan and per-check allocations (**Micro; matters only with many profiles**)

**Where:** `_availability` (`router.py:693-704`) is a linear `next(...)` scan.
It is called once per candidate/current check, 1–3 times per route. Measured:
0.44 µs for 3 profiles, 0.85 µs for 10, **6.0 µs for 100** (last element), so
with a large catalog and several checks this becomes comparable to the entire
route. `RoutingRequest` already rejects duplicate profile IDs and iterates the
same tuple at validation time; building a `{profile_id: availability}` mapping
once per request would make every later check O(1).

Related per-check allocations:

- `required <= set(profile.capabilities)` (`router.py:717`) allocates a set on
  every compatibility check. `required.issubset(profile.capabilities)` accepts
  the tuple directly; or store `capabilities` as a `frozenset` on
  `ModelProfile`.
- `_known_output_ceiling` (`router.py:830-841`) and `_known_context_limit`
  (`router.py:844-852`) build a list and call `min` on every check. Measured at
  0.16 µs each; leave them unless the surrounding path is tightened anyway.

### R8. Economy veto leaves `matched_rule_id` pointing at the vetoed rule (**Contract clarity**)

**Where:** `_turn_decision` (`router.py:440-449`) replaces the candidate with
the default and sets `reason_code=ECONOMY_VETOED`, but leaves `matched_rule_id`
as the rule that routed to the economy profile. The resulting decision says,
for example, `reason_code=economy_vetoed`, `matched_rule_id=routine-cheap`,
`target=normal`. That is defensible as audit provenance, but a consumer that
interprets `matched_rule_id` as “the rule that produced the target” will be
misled. Either clear it when the candidate is replaced, or document that it
records the matched rule before the veto. No test currently pins this field for
the veto case.

### R9. Switch that changes only the profile label (**Edge case**)

**Where:** `_active_decision` (`router.py:512-541`) compares ranks, not resolved
fields. Two profiles with different ranks but identical provider/model/effort/
output produce `action=switch`, `notify_on_applied=true`, and a full application
handshake even though the actual model call is unchanged. Reproduced by
configuring `normal` and `hard` with the same provider/model. The profile ID is
part of `EffectiveConfiguration`, so this is technically a configuration change
and the rank difference is user intent (for example, a future alias); the cost
is a redundant apply and a notification intent that the integration is expected
to deduplicate, so the visible impact depends on the integration. If this is not
desired, compare the resolved provider/model/effort/output (excluding
`profile_id`) and `keep` when they are identical. Treat as a config-edge
decision, not a bug.

### R10. Application-failure recovery builds a decision it may discard (**Micro, rare**)

**Where:** `_application_failure_decision` (`router.py:610-628`) builds the full
`_recovery_decision` (including a UUID and Pydantic validation), then discards
it to return `RECOVERY_EXHAUSTED` when the resolved target equals
`rejected_effective`. Compare the resolved target before constructing the
decision. This only fires on the exhausted path, so it is not worth much on its
own; it is listed for completeness.

### R11. Constant per-route costs (**Micro, for awareness only**)

Measured in-process with the example config and the static judge:

| Component | Cost |
| --- | --- |
| Whole `Router.route` | 16.2 µs |
| `asyncio.timeout` enter/exit | 1.9 µs |
| `uuid4().hex` | 1.0 µs |
| `RouteDecision` construction | 3.2 µs |
| `decision.model_copy(update=...)` | 0.9 µs |
| `match_rule` (4 rules) | 2.3 µs |
| Config digest at `Router` construction | 22 µs (once) |

None of these is worth changing for correctness or normal throughput. They are
listed so that future work does not “optimise” the wrong thing: the route is
already 16 µs, and `asyncio.timeout` costs about 12% of it. Skipping the timeout
for known-offline judges would need a judge capability flag and is not worth the
special case unless the router is called at very high QPS.

### R12. Checked, and *not* a problem

- `_state_incomplete` concatenates three tuples (up to 128 blocks). Measured:
  1.40 µs for concatenation vs 1.59 µs for `itertools.chain` and 1.59 µs for
  three separate `any` calls. Leave it.
- `_known_output_ceiling` / `_known_context_limit` allocations: 0.16 µs each.
- `set(profile.capabilities)`: 0.06 µs. Only worth changing for clarity.
- `_profile_value` (see policy) is the only place where a naive refactor is both
  clearer and measurably faster.

---

## 2. Configuration (`src/modelmux/config.py`)

### C1. Frozen config, mutable profile dictionary, cached digest (**Correctness**)

**Where:** `RouterConfig.profiles: dict[Identifier, ModelProfile]`
(`config.py:216`); `Router.__init__` caches `_routing_config_digest(config)`
(`router.py:390-393`).

`model_config = ConfigDict(frozen=True)` prevents attribute assignment, but it
does not freeze the dictionary itself. This works:

```python
cfg = load_config("examples/router.toml")
router = Router(cfg, build_judge(cfg.judge))
cfg.profiles["cheap"] = cfg.profiles["hard"]   # succeeds
# router keeps the old digest but now routes with the new profile
```

Reproduced: the router's digest stayed `b758643574f9…` while a newly constructed
router produced `a9f5bc1a3ceb…`. Because decisions attach the cached digest,
the digest no longer identifies the configuration that produced them. This
undermines the whole point of the digest and can make audit comparisons wrong.

**Suggested direction:** make the router own an immutable snapshot, for example
`MappingProxyType(dict(config.profiles))` (and use it for lookups), or validate
profiles into a frozen mapping type, or document and enforce “do not mutate a
config after constructing a Router”. A copy in `Router.__init__` is the
smallest change; a frozen mapping is the more robust one.

### C2. Unbounded `deadline_ms` overflows at route time (**Robustness**)

**Where:** `JudgeConfig.deadline_ms: PositiveInt = 1500` (`config.py:193`), used
as `self._config.judge.deadline_ms / 1000` in `router.py:411`.

`PositiveInt` is strict and ≥1 but has no upper bound. TOML can express
arbitrary integers, and a configuration such as `deadline_ms = 10**400` passes
validation and then raises `OverflowError: integer division result too large for
a float` on the first route. Reproduced. Add a sane upper bound (for example
`le=600_000`) or catch/convert. The same class of issue applies to other
unbounded numeric config fields, but `deadline_ms` is the only one that is
converted to a float.

### C3. Unbounded identifiers and text at the contract boundary (**Robustness / memory**)

**Where:** `Identifier = Annotated[str, StringConstraints(strip_whitespace=True,
min_length=1)]` (`types.py:17`). `MessageRecord.text` and `ToolRecord.text`
(`state.py:30-46`) are unbounded strings.

Every request/decision identifier (`request_id`, `session_id`,
`user_turn_id`, `profile_id`, `provider`, `model`, `failure_code`, …) can be
arbitrarily large. They are echoed into decisions and serialized, and the
decision schema has no max length. The task-state text is bounded by extraction,
but `MessageRecord.text` is not: `input_bytes` encodes the entire supplied text,
so a multi-megabyte tool output is encoded (and a temporary bytes copy made)
on every turn. The planned service request-size cap (2 MiB, not yet
implemented) mitigates but does not fix the core contract. Add a reasonable
`max_length` to `Identifier` (for example 128 or 256) and consider a cap or
explicit documented limit on input record text.

### C4. Plain `bool` fields accept arbitrary truthy strings (**Contract strictness**)

**Where:** `RoutingRequest.safe_to_apply_before_generation`,
`history_portable`, `recovery_used`; `RouteDecision.notify_on_applied`;
`BoundedText.truncated`, `source_truncated`; `TaskState` uses explicit
`NonNegativeInt` and `Score`, which are strict.

Pydantic's default bool validation is lax. This is accepted (reproduced):

```python
RouteDecision.model_validate({... "notify_on_applied": "yes" ...})
# -> notify_on_applied is True
```

The plan says to reject malformed numbers and invalid enums; booleans are not
explicitly mentioned, but a versioned boundary should not silently accept
`"yes"` for a fact. Add `Field(strict=True)` to the bool fields (or a shared
`StrictBool` alias) if the contract is meant to be exact. Note this is a
compatibility change for any consumer that sends `0`/`1`; the fixtures use real
booleans.

### C5. Minor config observations

- `ConfigError.problems` (`config.py:36-38`) is stored but never read anywhere
  in the repo. It is a reasonable public API for programmatic callers; keep it
  or drop it, but it is currently dead weight.
- `_error_location` (`config.py:264-268`) compiles a regex on every TOML error.
  Errors are cold; leave it.
- `JudgeConfig` permits a `[judge.static]` section while `selection = "jev"` (and
  vice versa). This is harmless but means an unused section is silently
  accepted. If the config should describe exactly one judge, reject the
  unused section; low priority.
- `RouterConfig._references_exist` correctly reports all dangling references in
  one pass; good error accumulation.

---

## 3. Shared types and task state (`src/modelmux/types.py`, `src/modelmux/state.py`)

### S1. State preparation scales with the entire supplied history (**Waste at scale**)

**Where:** `extract_task_state` (`state.py:48-117`).

Every call walks all supplied messages to find the latest user message
(`_latest_user_index`), builds a list of `(role, text)` tuples for all messages
before it (`history_records`), builds a second list of all supplied texts for
`input_bytes` (`provided_text`), and encodes every text to count UTF-8 bytes.
Only 3,000 characters of history and 64 records are retained.

Measured end-to-end build + extract + request construction, plus routing:

| Supplied history | Build + extract + request | Route (offline judge) | Total |
| --- | --- | --- | --- |
| 1 message | 16.7 µs | 15.1 µs | 31.8 µs |
| 10 messages | 39.3 µs | 15.6 µs | 54.9 µs |
| 100 messages | 112.1 µs | 15.7 µs | 127.8 µs |
| 1,000 messages | 995.4 µs | 17.3 µs | 1,012.7 µs |

Extraction alone is 24.7 µs / 47.0 µs / 199.8 µs / 975.2 µs for 10 / 100 /
1,000 / 5,000 messages respectively. Routing is constant. In a session where the
harness passes the whole conversation on each turn, the total work over N turns
is O(N²) in message count. It is still under a millisecond at 1,000 messages, so
this is not a latency emergency, but it is the only superlinear path in the
core and it grows with the thing sessions do most.

Part of the cost is record construction, not extraction (see S6). The two
practical directions:

1. **Bound the input at the call site.** The state budgets mean only the recent
   history can be judged. A caller can pass the last window (for example the
   last 64 messages) and still get exact counts for the *supplied* window,
   which is what the contract defines. Document this in the README; the current
   wording invites passing the whole conversation.
2. **Make extraction cheaper for the same input.** Avoid the intermediate
   `history_records`/`provided_text` lists; iterate indices or use
   `itertools.chain`; compute `omitted` as `len(blocks) - len(selected)`.

### S2. `_select` labels records that it is about to drop (**Waste at scale**)

**Where:** `_select` (`state.py:153-182`).

`label, label_truncated = _label(source)` runs **before** the budget/record-limit
decision. In a 100-message history, the profiler shows 101 `_label` calls per
extraction (one for the latest user plus 100 history messages) while only about
15 history blocks are retained; roughly 85 history labels are sliced and then
discarded. For a 5,000-message history that is 5,000 label operations per turn
for a few hundred useful ones at most. Move `_label` into the two `append`
branches (`len(text) <= remaining` and the truncation branch).

Also in the same loop:

- `omitted` is incremented per dropped record, but after the loop it is always
  exactly `len(blocks) - len(selected)` (every block is either appended or
  dropped). Computing it once removes the counter and its branches.
- `len(_TRUNCATION_MARKER)` is recomputed for every block. Hoist it to a module
  constant (`_MARKER_LEN`).
- Once `len(selected) >= limit`, no later block can be appended (the limit
  check comes first), so the loop can **break** immediately; the omitted count
  is then `len(blocks) - len(selected)`. The current loop scans every remaining
  block only to increment a counter that is derivable.
- When the budget is exhausted but the record limit is not, later *empty*
  blocks can still be selected (`0 <= 0`). That is the only case where the scan
  cannot simply stop. A cheap `if not text` fast path for the non-empty blocks,
  or documenting that empty records need not fill the quota, would make the
  remaining scan trivial. The tests currently rely on empty records filling the
  record limit (`test_extraction_bounds_many_empty_records`), so changing that
  specific behaviour is a decision, not a free optimisation.

### S3. `provided_text` materialises every text just to sum bytes (**Micro**)

**Where:** `state.py:96-112`. The list holds references (not copies), but it is
O(n) allocation and is immediately reduced by `sum`. Iterate
`itertools.chain(messages, instructions, tools)` directly. The `encode` cost
itself is inherent to reporting exact UTF-8 bytes.

### S4. `history_records` materialises every message before selection (**Micro**)

**Where:** `state.py:63, 75-77`. A list of `(role, text)` tuples as long as the
history is built even though at most 64 records are retained. The instruction
and tool comprehensions at `state.py:85-94` have the same shape: they materialise
every supplied record before `_select` drops most of them. This is part of the
O(n) allocation in S1. `_select` could accept the source sequence and an index
range, or a small generator of `(source, text)`; the simplest change for history
is to iterate `reversed(range(latest_index))` and index `messages` directly.

### S5. Tool names are labelled twice (**Micro**)

**Where:** `state.py:99-101`. `_label(item.name)` is computed for the first
`MAX_TOOL_NAMES` tools for `tool_names`, and `_select` computes the same labels
again for `tool_excerpts`. For 32 tools that is 32 extra slices per turn. Cheap,
but if `_select` is being touched anyway this can share the result.

### S6. Input records use Pydantic where a plain record would do (**Waste at scale**)

**Where:** `MessageRecord` / `ToolRecord` (`state.py:30-46`).

These are internal extraction inputs, not the versioned JSON boundary
(`RoutingRequest`/`TaskState` are). Measured construction cost:

| Operation | Cost |
| --- | --- |
| `MessageRecord(...)` | 0.52 µs |
| 100 records | 51.9 µs |
| 1,000 records | 677.2 µs |
| 1,000 equivalent tuples | 9.2 µs |

A harness that rebuilds records from its native message list every turn pays
approximately 0.5–0.7 µs per message, which dominates the ~0.2 µs/message
extraction cost. A `dataclass(frozen=True, slots=True)` or `NamedTuple` (with a
`str` role/name and no `strip_whitespace`) would remove that overhead while
keeping the validated boundary types strict. Trade-off: the current models
reject non-string roles and strip whitespace; those checks are cheap to
replicate at the harness adapter if needed. This is a design choice, but the
measurement is large enough to be deliberate.

### S7. The serialized-state byte cap re-serializes the whole state on every validation (**Waste at scale**)

**Where:** `TaskState._within_bounds` (`types.py:128-148`):

```python
if len(self.model_dump_json().encode("utf-8")) > MAX_STATE_BYTES:
```

Measured for a 12.6 KB state: `model_dump_json()` 6.7 µs, `len(...encode())`
6.9 µs, `pydantic_core.to_json(self)` 6.1 µs. For a max-size state this is
roughly 30% of `extract_task_state` (22 µs total) and about 25% of
`TaskState.model_validate_json` (25 µs). The check exists to reject
hand-built/deserialized states with pathological counts; states produced by
`extract_task_state` are already bounded by construction and by the category
sums, so the check cannot fail for them (the cap is arithmetically above the
four category budgets, as the comment explains).

Two low-risk improvements:

- Use `pydantic_core.to_json(self)` and compare `len()` on the returned bytes;
  it avoids the intermediate `str` and measured ~12% faster on the check.
- Keep the check at the boundary but do not run it for states just produced by
  extraction, if a trusted-construction path is acceptable. This needs care:
  Pydantic validators cannot distinguish trusted construction, and bypassing
  validation is exactly the kind of shortcut that tends to leak. The first
  option is the safer one.

Do **not** remove the check: the test
`test_direct_state_rejects_excessive_serialized_overhead` deliberately feeds a
huge integer through `model_validate`, which no field-level cap catches.

### S8. Minor state observations

- `_truncate` (`state.py:131-139`) assumes `cap >= len(marker)`; `_select`
  guards that, and `latest_user` uses 6,000, so it is safe today. It is a
  private helper with an implicit precondition; a one-line comment or a guard
  costs nothing.
- `latest_user.source` is always the literal `"user"` (the index search requires
  `role == _USER_ROLE`), so `latest_user.source_truncated` is always false. The
  field is retained for uniformity with other blocks; no action needed, just
  note that the `_label` call for it cannot truncate.
- The `TaskState` docstring is accurate about what the Python checks do and do
  not enforce; do not duplicate those checks in the JSON schema (they cannot be
  expressed there).

---

## 4. Policy (`src/modelmux/policy.py`)

### P1. A dictionary is rebuilt for every condition (**Micro, and a clarity win**)

**Where:** `_profile_value` (`policy.py:38-45`) constructs a four-entry dict on
each call, then indexes it. `match_rule` calls it once per condition; with the
example policy that is a handful of allocations per turn. Measured 0.146 µs vs
0.039 µs for the equivalent `getattr`; `match_rule` itself is 2.3 µs for 4
rules.

`SemanticField` values are exactly the `TaskProfile` field names, so:

```python
def _profile_value(profile: TaskProfile, field: SemanticField) -> float:
    return getattr(profile, field.value)
```

is simpler, removes the dict, and keeps the same closed set of fields (the enum
already constrains them). `_COMPARISONS` is fine as a dict; the lookup is not
the cost.

### P2. First-match evaluation is O(rules × conditions) (**Fine**)

The rule sets are user configuration and tiny. No indexing is warranted; a
first-match list is the clearest expression of the policy semantics.

---

## 5. Judges (`src/modelmux/judges/`)

### J1. Jev credential is resolved once at construction (**Operational**)

**Where:** `jev.py:118-121` caches `os.environ.get(config.api_key_env)`.

Documented in the README and `docs/jev.md`, so this is intentional. The
consequence is that rotating the key, or populating the environment after the
judge is built (common in a service that loads secrets after startup), requires
rebuilding the judge. If the service milestone constructs the router early, this
will surprise someone. Either re-read on each evaluation (a dict lookup, not a
syscall, and negligible against the HTTP call) or document the lifecycle
explicitly. Re-reading is arguably more robust and costs nothing measurable.

### J2. No response size cap before `response.json()` (**Robustness**)

**Where:** `jev.py:129-142`. `raise_for_status()` then `response.json()`. httpx
buffers the full response body by default, so a misbehaving or compromised
endpoint can make the process allocate an unbounded body. The service plan
requires a response-size cap; the adapter has none. A `Content-Length` check
plus a bounded read (or a streaming read with a cap) would close this. This is
unlikely for the documented API but is the kind of thing a remote judge adapter
should not leave to the caller's client configuration.

### J3. Fixed endpoint and no retries (**Intentional; verify it stays so**)

`_ENDPOINT` is a module constant; the router deadline bounds the call; redirects
are disabled per request. This matches the documented contract and is a
deliberate security choice (state must not be forwarded to a redirect target).
No change recommended. The "endpoint not configurable" limitation is documented.

### J4. Substring cue judge over-triggers (**Product cost, intentional V1**)

`rules.py:18-28` uses plain substring matching. `"design"` matches
`"designated"`, `"production"` matches a docs-only "production guide",
`"secrets"` matches "secrets manager" in a routine config edit. Combined with
the no-de-escalation policy, one false positive can pin a session to the hard
profile for the rest of the session. This is explicitly a conservative
heuristic baseline, and the plan forbids automatic de-escalation in V1, so it is
not a bug. It is worth recording as a cost risk: the rules judge is the default
offline judge, and its cue table is the cheapest place to reduce unnecessary
"hard" selections. If it is ever tuned, add routing-level tests for the
false-positive examples above.

### J5. `build_judge` duplicates config validation (**Acceptable**)

**Where:** `judges/__init__.py:22-32`. The `static is None` / `jev is None`
checks are unreachable for a validated `JudgeConfig` (its own model validator
enforces them). They also serve as type narrowing for mypy, so they are not
pure dead weight. Leave them, or replace with `assert ... is not None` if the
preference is for assertions over duplicated messages; the current ValueError
messages are more usable for a caller that used `model_construct`.

### J6. Payload construction (**Fine**)

`_request_payload` rebuilds the questions dict reference and the state lists on
every call. The questions are constant but small, and the state must be
translated per call. No caching is warranted; the network dominates.

---

## 6. Contracts and schema generation (`src/modelmux/schemas.py`, `contracts/`)

### K1. Schema generation never removes stale files (**Hygiene**)

**Where:** `write_schemas` (`schemas.py:24-29`) writes every model in
`SCHEMA_MODELS` but does not delete files no longer in the mapping. The drift
test (`test_contracts.py:54-57`) iterates only the current mapping. If a
boundary model is removed or renamed, its old `contracts/v1/*.schema.json`
remains in the repo and the suite still passes. Add a check that the committed
schema directory contains exactly the expected filenames (for example in the
drift test), or have `write_schemas` remove unexpected `*.schema.json` files.

### K2. Versioning is by directory only (**Contract clarity, low**)

The boundary schemas live under `contracts/v1/` and `RouterConfig.version` is
`Literal[1]`, but `RoutingRequest`, `RouteDecision`, and `ApplicationResult`
have no explicit contract-version field. A v2 consumer cannot be distinguished
at runtime from a v1 payload that happens to validate; the plan's “reject
unknown contract versions” is met only by `extra="forbid"` and the config
`version` literal. This may be intentional (a new schema path is the version),
but the README calls the boundary “versioned” without saying how a runtime
consumer knows the version. Document the convention, or add a `version` field
in a future v2.

### K3. Duplicated `allOf` branches in the decision schema (**Hygiene**)

**Where:** `_DECISION_SCHEMA_EXTRA` (`router.py:27-84`). The `keep` and `switch`
branches are identical except for the `const`, and the `defer` and `stop`
branches are identical except for the `const`. Merging each pair with
`"enum": ["keep", "switch"]` / `"enum": ["defer", "stop"]` removes about 20
lines of static schema data with no semantic change. The application-result and
request extras are already compact.

### K4. Schema strictness is good (**Checked**)

All three generated schemas use `additionalProperties: false`, `$defs` for the
nested models, enums for the closed sets, and the cross-field `allOf`
invariants. The fixtures validate. The runtime checks that JSON Schema cannot
express (category text sums, serialized byte cap) are correctly documented as
Python-only rather than silently implied to be in the schema. No change.

### K5. Test schema generation cost (**Not a problem**)

`test_contracts._schema()` regenerates `model_json_schema()` for every
parametrized invalid payload. The full suite is 0.66 s, so this is irrelevant at
the current scale; do not add a fixture cache solely for this.

### K6. Unused boundary surface (**Intentional, note for later**)

`ApplicationResult` is defined and schema-tested but not consumed by `Router`;
`last_applied_decision_id`, `integration_instance_id`, and
`application_failure_code` are accepted and never used in routing. This is
consistent with the plan (the service/integration milestones own sequencing and
acknowledgement). It is listed only so a future reader does not mistake the
fields for wired behaviour.

---

## 7. Tests (`tests/`)

### T1. The suite is fast; do not optimise it for speed (**Checked**)

`uv run pytest -q` → **200 passed in 0.66 s**. There is no test-performance
problem, no slow fixture, and no network. The timeout tests use a 20 ms deadline
and cancel 10 s sleeps deterministically.

### T2. `test_router.py` is 1,383 lines and repeats its arrange blocks (**Maintainability**)

The module has 70 test functions (more with parametrization), and each builds a
config, a request, and a fake judge. Many adjacent
tests differ only in one flag (for example the current-output tests at
`tests/test_router.py:711-808` and the context tests at 904-1112). This is not a
runtime issue; it is the main reason a change to routing policy requires editing
a large file. Parametrizing the near-duplicates (`current` output limit,
required output, context limit, availability) would cut the file substantially
and make the policy table easier to read. Do this only when touching those
tests, not as a standalone cleanup.

### T3. Duplicated helpers across test modules (**Minor**)

`EXAMPLE_CONFIG` (test_config, test_router), `_state()` (test_router,
test_judges, test_contracts, test_jev), and the profile/request/config builders
are duplicated with small variations. A `conftest.py` fixture for the example
path and a shared state helper would remove a little noise. Given the suite's
size and the AGENTS preference for obvious duplication over premature
abstraction, this is optional.

### T4. Coverage gaps that matter for efficiency work (**Verification**)

No test pins the exact R1 outcome (invalid current, judge failure, judgment
fallback unavailable while a compatibility fallback is available), the
`matched_rule_id` value under the economy veto (R8), the no-op-switch case (R9),
the mutable-config/digest behaviour (C1), or a bounded `deadline_ms` (C2). If any
of those are fixed, add one focused regression each; the existing suite is
otherwise strong on the documented invariants.

---

## 8. Documentation, config, and repository hygiene

### D1. `examples/router.toml` says rules and Jev are not implemented (**Stale, misleading**)

**Where:** `examples/router.toml:21-22`:

```toml
# runs work without credentials; the rules and Jev judges are not implemented.
```

Both are implemented (`judges/rules.py`, `judges/jev.py`), tested, and described
in the README. A user reading the example is told the opposite. Fix the comment
(for example: “the rules and Jev judges are available but need no credentials
for this example; Jev needs `[judge.jev]` and an API key”). This is the clearest
factual doc bug in the repo.

### D2. `tests/README.md` omits two test files (**Stale**)

The list ends at `test_router.py`; `tests/test_contracts.py` and
`tests/test_jev.py` exist and cover important boundaries (schema drift, Jev
error mapping). Add them.

### D3. `docs/PROGRESS.md` is a 736-line append-only log with no current summary (**Agent/context cost**)

AGENTS.md calls it “current handoff notes”, but roughly 60% of the
repository's prose documentation is a chronological log of ten milestones,
each repeating a “Created / Decisions / Verification” narrative. A new session
must read
hundreds of lines to find the last state, and historical sections contain
statements that were later corrected (the file itself says the earlier
“no unresolved findings” claim was wrong). Add a 10–15 line “Current state /
Next step” at the top, and move the historical detail to
`docs/PROGRESS_HISTORY.md` (or collapse old sections). This is the largest
non-runtime efficiency cost in the repo.

### D4. `docs/IMPLEMENTATION_PLAN.md` is the original prompt, not a current roadmap (**Hygiene**)

Its title is “CODEX SCAFFOLD PROMPT — revision 3”, and it lists files that do
not exist (`events.py`, `cli.py`, `service.py`, `benchmarks/`, `.env.example`,
`integrations/opencode/`). That is acceptable as a historical design document,
but it is not a useful roadmap for the next milestone without a short “what is
done / what is next” delta. Keep the prompt if it is the contract; add a
one-page current checklist or point to the PROGRESS summary.

### D5. `pyproject.toml` description is stale (**Hygiene**)

`description = "Scaffold for a harness-independent model routing core."` — the
core is implemented. Update the description (and the package version if the
project is tracking milestones) so tooling and package metadata do not advertise
a scaffold.

### D6. CI and local checks disagree on scope (**Hygiene**)

`ci.yml` runs `ruff format --check src tests`, `ruff check src tests`, and
`mypy` (configured with `files = ["src"]`). PROGRESS repeatedly records
`ruff format --check src tests scripts` and `ruff check src tests scripts`, and
the script is currently clean, but CI will not notice if it drifts. Include
`scripts` in the ruff steps, and decide whether `tests` should be type-checked
(`mypy` currently only covers `src`). This is cheap and closes a real gap.

The lint selection is also narrow (`E4`, `E7`, `E9`, `F`). That is why the
unused `reason` parameter (R5) is invisible to the configured check. Adding `ARG`, `B`, and
`SIM` (or a curated subset) would catch that class of dead parameter and
simplification without adding any dependency. For calibration, the broader run
in this review produced `ARG002` on R5 plus the intentional unused `state`
argument in `StaticTaskJudge.evaluate`, four `SIM102` hits in
`_settings_compatible`, three complexity warnings (two `PLR0911` and one
`PLR0912` across `_active_decision` and `_settings_compatible`), an unsorted
`__all__`, and one `PERF401` false positive in the reference-validation loop —
a small, mostly actionable set.

### D7. README placeholder drift (**Minor**)

The README snippet uses `model = "example-provider/normal-placeholder"` while
`examples/router.toml` uses `normal-placeholder-1`. The snippet is reduced, so
this is only cosmetic, but a copy/paste user will see a different model ID from
the file the text calls the complete starting point. Align the names.

A related wording nuance: “unknown facts are not treated as verified” is
accurate but easy to read as “unknown facts are rejected”. The code
intentionally accepts unknown effort/output/context facts and rejects only
*known* incompatibilities (the 1C repair records this as “unknown facts stay
None and never cause a positive rejection”). Say that explicitly; otherwise a
harness author may over-report facts or assume a profile is ineligible when it
is not.

### D8. `.gitignore` is a generic Python template (**Minor**)

It is ~200 lines and carries Django, Flask, Scrapy, Celery, Sage, Spyder, Rope,
mkdocs, Pyre, pytype, Cython, PyCharm, Abstra, Marimo, and Streamlit sections
for a project that uses none of them. It does correctly ignore `.venv`,
`.mypy_cache`, `.ruff_cache`, and `.pytest_cache`, and it keeps `uv.lock`
tracked. Trim it only if you want to reduce noise; no functional problem.

### D9. CI has never run remotely (**Verification gap**)

Milestone 0's PROGRESS entry says “Remote GitHub Actions were not run, so this
records local verification only.” The workflow pins `actions/checkout@v7` and
`astral-sh/setup-uv@v10.1.0`; if either tag is wrong, the first remote run
fails, but the local suite cannot reveal it. This review cannot verify
network-hosted action versions; treat the first remote run as a required check,
not an assumption.

### D10. README routing example uses top-level `await` (**Doc usability**)

The snippet ends with `decision = await router.route(request)` at module level,
which is a `SyntaxError` in a plain script. PROGRESS notes that the example was
verified “unchanged in an `asyncio.run` wrapper”, but the README does not show
the wrapper or say that the code must run inside an async context. Add the
`asyncio.run(...)` line (or wrap the last statement in `async def main()`), so a
copy/paste user gets a runnable example on the first try.

---

## 9. Measured baseline (for future comparisons)

All times are in-process on CPython 3.12.13 / pydantic 2.13.5, example config,
static judge, warm loops.

| Path | Cost |
| --- | --- |
| `Router.route` (small state) | 15–17 µs |
| `Router` construction (config digest) | ~22 µs |
| `extract_task_state`, 1 message | ~8 µs |
| `extract_task_state`, 10 messages × 200 chars | 24.7 µs |
| `extract_task_state`, 100 messages × 200 chars | 47.0 µs |
| `extract_task_state`, 1,000 messages × 200 chars | 199.8 µs |
| `extract_task_state`, 5,000 messages × 200 chars | 975.2 µs |
| `TaskState.model_validate_json`, small (352 B) | 7.0 µs |
| `TaskState.model_validate_json`, large (12.6 KB) | 25.1 µs |
| `TaskState.model_dump_json`, large | 6.7 µs |
| `pydantic_core.to_json`, large | 6.1 µs |
| `match_rule`, 4 rules | 2.3 µs |
| `_availability` scan, 3 / 10 / 100 profiles | 0.44 / 0.85 / 6.04 µs |
| `asyncio.timeout` enter/exit | 1.9 µs |
| `uuid4().hex` | 1.0 µs |
| `RouteDecision` construction | 3.2 µs |
| `model_copy(update=...)` | 0.9 µs |
| `MessageRecord` construction | 0.52 µs |
| 1,000 `MessageRecord`s | 677.2 µs |

These numbers are the reason for the ordering of recommendations: fix R1/R2
(wasted judge calls), C1/C2 (correctness), and S1/S2 (the only scaling path)
before touching R6/R7/P1 (micro-optimisations).

## 10. Suggested order of work

1. **R1 / R2** — decide the invalid-current + judge-failure policy, skip or
   redirect the judge call, add a regression test, and apply the same
   precondition check to the first-turn unsafe path. This is the most
   consequential reliability finding and carries a potentially paid cost.
2. **C1** — make the router own an immutable profile mapping (or copy it), so
   the cached digest cannot go stale.
3. **C2 / C3** — bound `deadline_ms` and `Identifier` lengths; decide on a
   record-text cap. Small validation changes with real robustness value.
4. **S1 / S2** — avoid the intermediate history/text lists in extraction, move
   `_label` into the append paths, compute `omitted` once, and document the
   option of passing a bounded window. Measure again at 1,000 messages.
5. **R4 / R5 / R6** — collapse the fallback gate, drop the unused `reason`
   parameter, and resolve the output limit once. Add no new tests beyond the
   existing router cases; they already cover the branches.
6. **D1 / D2 / D3** — fix the stale judge comment, the tests README, and add a
   current-state summary to PROGRESS. These cost little and pay back on every
   future session.
7. **Remaining micros and edge cases** (R7, R8, R9, P1, K1, S7's `to_json`,
   S5, S6) — only if the router becomes a high-QPS shared service, or
   opportunistically when the surrounding code is touched. Do not turn them
   into a standalone project.

## 11. What is already efficient and should be left alone

- The router is stateless, async-safe, and constant-time in task-state size (it
  never copies or rescans the task text); the only superlinear cost is state
  preparation, not routing.
- Cancellation and deadline handling are correct: `asyncio.timeout` cancels the
  in-flight judge, caller cancellation propagates, and programmer exceptions are
  not swallowed (`test_common_programmer_error_is_not_swallowed`,
  `test_caller_cancellation_propagates`).
- Error handling is narrow and secret-safe: `ConfigError` suppresses parser
  context, Jev errors contain status codes only, and decisions contain no prompt
  text or credentials. The tests explicitly check these.
- The generated schemas are strict, cross-field invariants are expressed in both
  Pydantic and JSON Schema, and the drift test is a real guard.
- The Jev adapter disables redirects per request, preserves the injected client
  ownership model, and has offline mock tests that cover status, transport,
  invalid JSON, schema mismatch, missing credentials, cancellation, and the
  router fallback.
- Extraction is deterministic, bounded, and does not read files, environment, or
  URLs; the truncation metadata is consistent and tested.
- The test suite is fast, offline, and uses no keys.
- Files read and found to need no efficiency change: `src/modelmux/__init__.py`
  (one-line docstring), `src/modelmux/judges/base.py` (small Protocol and error
  type), `src/modelmux/judges/static.py` (builds its profile once and reuses
  it), `scripts/generate_contract_schemas.py` (thin wrapper),
  `docs/jev.md` (accurate and bounded), `LICENSE`, `uv.lock`, and the three
  contract fixtures. This list is explicit so “no findings” is not confused with
  “not read”.
- Dependencies are minimal and locked; the service extra is genuinely optional
  and no core module imports `starlette` or `uvicorn`.

---

## Appendix: reproduction notes

The measurements above were produced with ad-hoc scripts under
`/tmp/opencode/mm/` (not committed). The important commands were:

```sh
uv run pytest -q
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run ruff check src --select PERF,SIM,C4,RUF,PLR0911,PLR0912,PLR0915,FURB,TRY300,TRY301,B
uv run ruff check src --select ANN,RET,ARG,ERA,PLW,PLC
```

The two behavioural probes worth repeating are:

1. **Mutable config / stale digest**

   ```python
   cfg = load_config("examples/router.toml")
   router = Router(cfg, build_judge(cfg.judge))
   before = router._config_digest
   cfg.profiles["cheap"] = cfg.profiles["hard"]
   assert router._config_digest == before
   assert Router(cfg, build_judge(cfg.judge))._config_digest != before
   ```

2. **Invalid current: judge success vs failure**

   Use a request with `current` set to a profile that is absent from
   `available_profiles`, `compatibility_fallback="hard"`, and
   `judgment_fallback="normal"`. A successful judge returns
   `switch/compatibility_fallback/hard`; `JudgeError` returns
   `stop/no_compatible_profile`. See R1.

These are documented here because the exact outcomes are not pinned by the
committed suite and are the two findings most likely to be disputed without
evidence. (`test_judge_failure_fallback_respects_bound_history` covers a
different invalid-current scenario, so it does not protect R1.)
