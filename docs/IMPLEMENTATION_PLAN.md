# CODEX SCAFFOLD PROMPT — revision 3

Implement a small open-source model router, working name modelmux. This prompt is self-contained and replaces the earlier gateway/manual-pinning scaffold entirely. Read applicable AGENTS.md, preserve unrelated work, implement and test without publishing or deploying. Do not build a platform.

## Product and acceptance criterion

After one-time installation/configuration, the user opens OpenCode normally, types a prompt, and works normally. The router automatically selects model and reasoning settings. If the effective configuration changes, the user receives a short native harness notification after successful application. No per-task launcher, decide command, model menu, manual pinning, or approval of routine model choices. Tools and permissions continue to belong to the harness.

Core routing and harness integrations must be separate parts of the repo. The core decides; integrations report facts and apply decisions. OpenCode is the first integration, never the foundation of the core. Other harnesses later implement the same versioned contract. Do not claim Codex CLI support in this version.

The required behavior is tested against a pinned OpenCode version. A mocked plugin test or HTTP proxy demonstration is not proof that real OpenCode applies a model change.

## Milestone 0: scaffold the core

Milestone 0 prepares a small, reproducible Python project in which the harness-independent core can be built. It contains no harness integration work. Do not inspect or prototype OpenCode hooks, add TypeScript integration dependencies, build a model gateway, or make the core depend on facts from one harness in this milestone.

Create the Python package and development foundation: pyproject.toml, uv.lock, src/modelmux/__init__.py, the tests directory, Ruff, mypy, pytest, and the Python part of CI. Declare Python >=3.11, the planned core dependencies, and the optional service dependencies so importing modelmux does not require the HTTP service extra. Preserve the existing license. Add only files needed for a working scaffold; do not create empty placeholder modules for every later component.

Establish the source of truth for the future core boundary without implementing routing behavior. Reserve contracts/v1/ for harness-neutral request, decision, and application-result schemas, but define those contracts with the core in the next milestone. OpenCode names, events, SDK types, and lifecycle details must never appear in core package APIs.

Milestone 0 is complete when a clean checkout can install locked dependencies and run the configured Python formatting, lint, typecheck, and test commands successfully. The package must import without service extras. No model selection, judge, policy, service endpoint, adapter, plugin, or end-to-end harness claim is expected yet.

## Architecture and stack

One repo:

    pyproject.toml
    uv.lock
    README.md
    LICENSE
    .env.example
    .gitignore
    .github/workflows/ci.yml
    src/modelmux/
      __init__.py
      types.py
      config.py
      state.py
      policy.py
      router.py
      events.py
      cli.py
      service.py
      judges/{base,static,rules,jev}.py
    contracts/v1/
      routing-request.schema.json
      routing-decision.schema.json
      application-result.schema.json
      fixtures/
    integrations/opencode/
      package.json
      bun.lock
      tsconfig.json
      src/{index,client,translate}.ts
      tests/
      README.md
      COMPATIBILITY.md
    tests/
    examples/router.toml
    benchmarks/{README.md,tasks.jsonl,replay.py}

Merge tiny modules if clearer. Do not create empty implementations of other harnesses. Add gateway/provider modules ONLY if the integration proof establishes a transport requirement. No universal provider protocol translation.

Core: Python >=3.11, uv, Pydantic v2, stdlib tomllib/argparse/logging, httpx. Optional service extra: Starlette and uvicorn. Development: pytest, pytest-asyncio, Ruff, mypy. Core imports must work without HTTP service extra.

OpenCode integration: TypeScript, Bun, actual pinned OpenCode plugin/SDK dependencies. This second language is justified only by the harness ecosystem. No duplicate policy/judge logic. Use a versioned JSON boundary, checked schemas and shared fixtures; runtime validate received data, not TypeScript assertions alone. Derive JSON schemas from authoritative Python boundary types and CI-check drift. Keep TS validation small; a schema validator is acceptable. Avoid a separate schema generation framework.

Small modules, explicit typing, dependency injection, async resource ownership, no global mutable state. Prefer Protocols to framework hierarchies. Target a small core, but never sacrifice cancellation correctness for an arbitrary line count. MIT license unless existing instructions specify otherwise.

## Separation of responsibilities

Core owns semantic questions, profiles, selection, keep/switch/defer decisions, compatibility, hysteresis, fallback selection, cache tradeoffs, and machine-readable notification intent.

Integration owns extracting already available harness facts, reporting capabilities/current effective model, recognizing factual safe boundaries, translating controls, applying a returned decision, reporting success/failure, and rendering notices. It must not contain thresholds, classify tasks, select fallback models, decide escalation, or reinterpret the core's reason into new policy.

The integration can retain bounded operational state (session revision, last application ID, applied config, turn pin) to implement the protocol. This is not permission to store prompts indefinitely or move routing decisions into the adapter. No database/session service.

Default transport: loopback decision service. Generation stays native to the harness where possible, so generator credentials need not transit the routing service. A custom script can import the Python core directly. No mandatory model gateway. If needed for OpenCode, a gateway is an optional execution adapter with the restricted behavior below.

## Core contracts

Frozen typed models where practical; separate facts, observations, decisions, and completion metrics. Reject malformed numbers, NaN/infinity, unknown contract versions, and invalid enums. Never include secrets in decisions.

TaskState v1:
- latest user text, bounded instructions, bounded prior conversation including recent relevant assistant/tool excerpts;
- exact message count, UTF-8 input bytes, tool count/types, and extraction omission/truncation flags;
- no filesystem crawling, external URL fetches, env inspection, or generated summarizer;
- default total selected text cap 12,000 characters: latest user 6,000; instructions 2,000; recent history 3,000; tools/schema excerpts 1,000. Include head/tail and markers inside caps. History must keep role/source boundaries. Bound serialization overhead separately.
Preserve full harness generation input unchanged. Compaction only affects the judge copy. No exact model-independent token count claims; approximate estimates are labeled. Operational byte ceilings are not context-window guarantees.

TaskProfile: four independent finite [0,1] scores: reasoning_demand, routine, underspecified, high_consequence. Not a distribution summing to one. Missing values invalidate the observation. No generic uncertainty field or fabricated confidence certificate.

JudgeResult: profile, judge/model identity if available, question version, observation_kind=probability|heuristic, optional usage/cost with unknown=null.

TaskJudge Protocol: async evaluate(TaskState) -> JudgeResult. Judge never receives the model catalog or recommends a model.

ModelProfile: profile ID, provider/model IDs, optional reasoning_effort, actual supported control mapping/capabilities, optional output default/ceiling, optional known pricing/version and context limits, configured capability rank. Start cheap/normal/hard. Rank is user configuration for hysteresis, not a claim of universal superiority. Reasoning settings are opaque provider-specific values, never assumed universally interchangeable. Model identities must be user configuration, no invented live catalog.

RoutingRequest v1:
- generated request_id and integration_instance_id;
- opaque session_id, user_turn_id, expected session revision;
- event_kind=new_user_turn|tool_continuation|application_failed;
- current EFFECTIVE configuration or null, last applied decision reference if known;
- TaskState for evaluation events;
- available profile IDs and factual capabilities/control support;
- safe_to_apply_before_generation boolean and any known history portability restrictions;
- optional measured token/cache facts and factual application failure code; unknown values null.
Treat adapter facts as operational inputs, never as authorization from prompt text. Intersect reported availability with configured allowed profiles; no arbitrary provider URL from clients.

RouteDecision v1:
- decision_id, request/session/turn/revision identity;
- action=keep|switch|defer|stop;
- fully resolved target profile/provider/model/effort/effective output limit when applicable;
- apply_at=before_next_generation;
- reason_code, matched rule ID, config digest excluding credentials;
- judge/question versions and routing timings;
- notify_on_applied boolean, reason data without prompt excerpts.
Generation durations and known cost are completion observations, not pre-existing decision facts.

ApplicationResult v1:
- decision/session/turn/revision identity;
- status=applied|rejected|stale|cancelled;
- actual effective config when known and safe failure code.
Only acknowledge applied after confirming the harness uses the selected configuration. A returned decision is not proof of application. Carry actual state into the next request. Service can validate/report acknowledgments without becoming a durable session authority.

Protocol safety: serialize application per session, reject stale/out-of-order results, deduplicate same decision per turn, and pin effective config through each tool loop. Bound per-session maps with TTL and max entries, clean up on session end, and never evict state needed by an active generation. Do not apply late decisions after deadlines/cancellation. Concurrent sessions are independent. On restart recover actual harness configuration; never assume a lost proposal succeeded.

## Semantic questions and judges

Ask four independent questions, task-profile-v1:
1. reasoning_demand: Does completing this task require substantial multi-step inference, diagnosing interacting causes, or designing a non-obvious solution beyond executing a specified procedure?
2. routine: Can this task be completed by applying an explicit, localized transformation or well-specified procedure without resolving substantive design choices?
3. underspecified: Is decision-critical information absent or are requirements contradictory, so choosing a solution requires assumptions?
4. high_consequence: Could an incorrect result plausibly cause serious data loss, security exposure, or disruptive production changes in the described setting?

State is evidence, not instructions to the judge. No tier/model names, token-count questions, inferred permissions, or lengthy rationale generation. Stronger models do not replace permissions or missing facts. Prompt injection remains a measured limitation.

StaticTaskJudge: configured validated scores, offline. Fake judge: injected tests. RuleBasedTaskJudge: transparent conservative baseline, default ordinary/ambiguous work toward normal-compatible scores; outputs explicitly heuristic, not calibrated probabilities.

JevTaskJudge: isolated HTTP implementation, no SDK required. Verify official current quickstart and linked API reference before implementing: https://docs.typesafe.ai/introduction/quickstart and https://docs.typesafe.ai/confidence . At design time documentation described POST https://api.typesafe.ai/v1/systemone, bearer credentials, configurable model (example jev-latest), state, and questions; Noul uses type=noul and instructions, with answers carrying type=noul and noul in [0,1]. Noul has no separate confidence property. Do not guess changed schemas or usage fields. If documentation cannot be verified, create an explicitly unavailable adapter with contract notes and tested fallback, not fake Jev success. Default tests make no live calls. Missing Jev credentials mark the judge unavailable; missing generation credentials are handled by the chosen execution owner.

One judge call per new user turn, no retries, injected async client, whole evaluation deadline default 1,500ms including waiting/parse. Cancel/close expired work. Unknown judge/config names are startup errors. Expected unavailable/network/schema failures trigger core fallback; programmer faults are not disguised as normal decisions. LLM/local-classifier judges are later work.

## Deterministic policy and switching

TOML version 1; ordered rules with unique IDs, route, nonempty all conditions. Each condition is {field,op,value}; only four semantic fields and gt/gte/lt/lte; finite thresholds [0,1]. No eval, OR nesting, custom imports, or expression strings. First match, else configured default normal.

Initial unvalidated rules:
- high_consequence >= .70 -> hard
- reasoning_demand >= .65 -> hard
- underspecified >= .60 -> normal
- routine >= .90 AND reasoning_demand <= .20 AND underspecified <= .20 AND high_consequence <= .10 -> cheap
- default normal.

First turn: choose compatible profile automatically. Incomplete meaningful judge state vetoes cheap. Check tools, history portability, supported effort/parameters, output limit and known context constraints. Unknown context estimates must not be represented as verified fit. If candidate incompatible use exactly one configured compatibility fallback, validated identically; otherwise stop with no_compatible_profile. Never discard tools/history to force a fit.

New user turn with active config: evaluate semantic profile against relevant conversation, then apply switching policy in CORE. Initial policy allows escalation at safe user-turn boundaries; no automatic within-session de-escalation. Same or lower-rank candidate -> keep, unless current becomes incompatible. Higher candidate -> switch only if safe and compatible with existing history; otherwise defer/stop as appropriate before generation. Tool continuations retain the applied route without rejudging. An idle session event alone does not establish a new semantic task. Start a fresh session for independent tasks; automatic new-task classification is deferred.

Jev failure: retain current compatible config; if none, use configured judgment fallback (normal by default, user may choose hard). Invalid current config uses compatibility handling. Core computes fallback, not integration.

Application failure: no automatic generation retry. Send rejection facts to core before generation; core may propose one compatible fallback or retain current config. Bounded attempts, no loops. Once generation starts, do not replay with a second model. Service unreachable: use an already-applied, core-authorized continuation only if its validity permits; otherwise surface an availability error rather than letting the plugin invent a model. Make any offline fallback an explicit core-issued/configured execution instruction, not adapter heuristics.

Notifications: initial selection may show one short notice; later only effective model/effort changes. Emit after successful apply, deduplicate, use native UI out of band. Do not insert routing notices into model messages. Hide scores by default. Failure notices are separate from successful switch notices.

No user-facing routine thinking-mode or profile choices after setup. Advanced explicit overrides may be documented but are not required for the flow; do not build an override UI in V1.

## Cache behavior and cost integrity

Do not implement your own semantic cache. Preserve prompts, instructions, tool definitions/order, and provider-native supported cache settings. No router IDs, timestamps, reasons, or synthetic messages inserted into generation prefixes. In a native integration retain existing provider request preparation and credentials; verify model switching does not accidentally retain incompatible provider options.

Assume model/provider cache state is not portable. Do not assert a warm hit from merely having used a model. Keep unknown cache facts null. V1's no-de-escalation policy reduces fragmentation; do not build an elaborate cost optimizer on speculative hit estimates. Core owns later cache-aware economic decisions, integration only reports observations.

Collect usage from native harness/provider events when available, including uncached/cached input, output, cache writes, and provider-specific billing categories. Record provenance. Never double count reasoning/output or cache categories. Unknown or incomplete billing -> cost null, not zero. Configured estimates clearly differ from invoices. Record cold/warm context conditions in benchmarks. If a transport path cannot observe streaming usage, document unknowns and do not make savings claims from it.

## Local service/config/operational security

Optional control service endpoints: POST /v1/route, POST /v1/applications, GET /healthz. These are our own contract, not OpenAI endpoints. validate and decide commands may exist for development/diagnostics but not normal user workflow.

CLI: modelmux validate --config examples/router.toml; modelmux serve --config ... . One-time setup can install/enable plugin and configure starting the service. Document the tested lifecycle. Do not require a per-task service launch or user-selected profile. Prefer integration-managed child service if reliable on the tested platform: fixed executable/argument array, no shell interpolation, bounded readiness, explicit ownership/shutdown, no killing unrelated services. A preconfigured persistent service is acceptable if startup is automatic and documented. Do not force systemd or add a process management framework.

Loopback default, refuse nonloopback bind V1; shared bearer token for local control service with constant-time checking, generated at setup and not exposed in task state. No accounts/auth platform. Deny browser Origin requests, validate Host, no permissive CORS. Fixed configured destinations; HTTPS remote judge, HTTP only explicit loopback; disable redirects/trust_env unless deliberately configured. Cap request size while reading (2MiB default), concurrency (8), queueing and response size. Fail fast saturation rather than unbounded backlog. Validate all profile/provider references, unknown keys, bounds, rank, URLs, effort/output limits at startup. Environment variables only through explicit credential names, not arbitrary interpolation. Do not echo validation input or raw exceptions.

Example config must include three clearly fictional replaceable profile IDs/models, static judge so core/contract tests and service run with no Jev key, policy, judge deadlines, service settings and switching.mode='escalate_only'. Clearly distinguish mock generation from real providers. .env.example empty credential placeholders only. Do not imply a ChatGPT subscription funds arbitrary model APIs.

Structured JSON logs to stderr: route_decided, decision_applied/rejected, generation_finished when observed. request/session identity (opaque), scores, observation kind, versions, safe reasons, actual vs proposed config, timing, usage/cost nullability. No prompts, code, tool arguments/results, secrets, cookies, raw errors or full URLs. Silence unsafe framework debug/access logs. Diagnostics stdout is structured. Full prompt logging is not a V1 feature.

## Harness integration phase

Begin harness integration work only after the harness-independent core, contracts, policy, judges, local service, and core tests are complete. Each harness is a separate consumer of the same versioned contracts. Do not change the core around one harness's SDK types or lifecycle. Add integrations only for harnesses explicitly selected for support; do not create empty integration packages.

Before implementing the OpenCode integration, inspect the pinned version's official plugin/SDK documentation, published types, and relevant source call sites. Record the tested version, exact extension points, event order, and limitations in integrations/opencode/COMPATIBILITY.md. Sources to begin:
- https://opencode.ai/docs/plugins/
- https://opencode.ai/docs/sdk/
- https://opencode.ai/docs/providers/

Official docs establish plugins and notification events, but this prompt does NOT assert a verified mutable pre-generation model-selection hook. Do not assume mutating a hook input changes the model actually used. Check whether the model has already been resolved at that call site; confirm changes propagate to request preparation and subsequent tool turns.

Prove a minimal end-to-end slice using a static core decision and distinguishable mock upstreams: ordinary user prompt -> decision -> selected model AND effort applied before generation -> tool continuation uses same config -> native notice after application. Preserve prompt ordering, cancellation, history, tool-call IDs, and one generation per user turn. Verify model metadata (context limits/tool support/compaction) agrees with the real selected configuration.

Prefer native harness model selection with the harness handling generation. If a harness cannot apply the core contract directly, assess the smallest adapter needed between that harness and the core. For OpenCode, if public hooks cannot support the required behavior, assess whether a thin local transport plus plugin can implement the same UX WITHOUT incorrect harness metadata or state. Policy may not move into an adapter or plugin.

If neither native hooks nor a thin adapter works, identify and prepare the smallest reviewable harness extension patch with a pinned version and rationale. Put OpenCode patches in integrations/opencode/patches/. Do not silently substitute a launcher, document an unimplemented API as available, or claim stock support when a patch is required. Report concrete integration limits rather than manufacturing success. No permission is needed for local inspection or prototypes; do not spend API credits by default.

## Optional gateway only if required by proven integration

Do not automatically build it if native selection works. If needed, put it in src/modelmux/gateway.py and providers/chat.py, independent of integrations/opencode. Support only necessary Chat Completions text/function-tool subset, streaming/nonstreaming, plus health/models if needed by harness. No Responses translation/native Anthropic adapter. Explicit versioned adapter metadata ties generation to an APPLIED route/turn; normal OpenCode continuations must work. No legacy 'auto history => 409' restriction; task boundaries now come from integration. Strip internal metadata upstream.

Validate supported fields, preserve full messages/tool calls/results and actual model IDs in responses. Set configured model/effort/output settings coherently with the harness; reject incompatible fields rather than silently dropping. Explicitly allow verified provider cache controls; do not blindly forward unknown vendor parameters. Do not forward inbound Authorization/cookies; use configured upstream credentials.

Use async HTTP context lifetime through stream, backpressure, bounded bodies, cancel on disconnect, release concurrency exactly once. Inspect initial upstream status before emitting headers. No generation retries. Forward SSE bytes without rebuilding deltas/[DONE]. Failures after headers close stream and log incomplete; never fake completion. Default connect=5s, idle=120s, total=600s, configurable. Expected provider errors preserve status with safe source/provider code; local validation errors identify router. Do not log upstream bodies. Actual token usage may come from native integration instead of parsing SSE; unknown remains null.

## Testing and verification

No keys/network required for normal tests. CI checks Python Ruff/mypy/pytest, integration TypeScript typecheck/tests, and schema/fixture compatibility. Lock actual compatible dependencies rather than invent versions.

Core tests: threshold equality/order/default, four score validation, extraction cap/source boundaries, incomplete-state economy veto, eligibility, fallback, rank/hysteresis, no tool-loop rejudge, retain-current judge failure, incompatible history, apply failure, stale revisions, unknown contract versions, secret-safe logs.

Integration tests: sends facts without policy; translates decisions; actual selected model AND effort; no notice before apply; notification deduplication; rejected decisions not marked applied; cancellation; concurrent sessions; stale results; restart recovery; native metadata/compaction consistency; cache options preserved; no synthetic routing messages. Use contract fixtures and real SDK types. Prove control-service deadlines do not create late switches.

Mandatory real OpenCode integration smoke with controlled mock upstream(s): launch ordinary OpenCode with plugin enabled, submit routine task -> core selects cheap, subsequent difficult user prompt -> core selects hard and one native notice, tool continuation -> hard without rejudge, no duplicate generations. Verify actual outbound model/effort and harness state. No paid model needed. Record exact commands/version and observed evidence. If a headless smoke cannot verify the native notice, distinguish automated transport evidence from UI verification and report the gap. Do not label this complete until required user flow is demonstrated or clearly report the blocker.

If gateway required: meaningful nonstream and SSE tests for early emission, awkward chunk boundaries/tool deltas, status before headers, midstream failure, cancellation and capacity release. Use real loopback fixture where ASGI test transports buffer streams.

Benchmarks: 24 authored tasks with semantic annotations, family/repository group, provisional acceptable-profile labels, label_status='hypothesis', acceptance rubric. Offline replay of static/rules/recorded observations only by default. No live spending. Document extension to 60-100 real tasks with group-disjoint held-out split, fixed model/effort/harness budgets, repeated runs, acceptance tests and blinded review when needed. Empirical acceptable-profile SETS, unknown when evidence insufficient; human 'hard' labels aren't ground truth. Compare fixed cheap/normal/hard, rules, Jev, later cheap LLM judge and direct-tier ablation. Measure full-task success/cost including judge, retries/rework, caching, escalations, p50/p95 overhead and uncertainty. Frozen transcripts only evaluate classifier decisions, not causal agent outcomes. Do not claim savings based on tier selections.

## Delivery order and limits

1. Core-only Python project scaffold and development tooling.
2. Harness-neutral core contracts, state extraction, policy, judges, router, and offline tests.
3. Versioned local service, CLI, operational limits, and core lifecycle tests.
4. Add an adapter only if a chosen harness cannot consume the core contract directly.
5. Implement and verify each explicitly supported harness integration; OpenCode is the first V1 integration.
6. End-to-end demos, privacy/cancellation checks, benchmark scaffold, and documentation.

Finish the core before beginning harness-specific feasibility or implementation work. Do not build a gateway or adapter unless a chosen harness proves it is needed. No dashboard, database, queues, cloud deployment, training, semantic cache, automatic de-escalation, or mid-tool-loop escalation in V1. No duplicated route heuristics in harness integrations. Future integrations use the same versioned contracts.

README: one-time setup, then 'open OpenCode and type'; optional diagnostics; actual compatibility/version/patch requirements; static/rules/Jev configuration; privacy including external judge transmission; fallback limits; costs as measured/unknown. Final report: files, exact setup and verification commands, actual test results, whether stock or patched OpenCode, observed UX, limitations. Clearly distinguish documentation, mocks, real harness tests and live provider tests. Do not claim plugin feasibility or cost savings without evidence.
