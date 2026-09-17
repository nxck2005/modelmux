Implement a small open-source model router, working name modelmux. This prompt is self-contained and replaces the earlier gateway/manual-pinning scaffold entirely. Read applicable AGENTS.md, preserve unrelated work, implement and test without publishing or deploying. Do not build a platform.


Product and acceptance criterion

After one-time installation/configuration, the user opens OpenCode normally, types a prompt, and works normally. The router automatically selects model and reasoning settings. If the effective configuration changes, the user receives a short native harness notification after successful application. No per-task launcher, decide command, model menu, manual pinning, or approval of routine model choices. Tools and permissions continue to belong to the harness.


Core routing and harness integrations must be separate parts of the repo. The core decides; integrations report facts and apply decisions. OpenCode is the first integration, never the foundation of the core. Other harnesses later implement the same versioned contract. Do not claim Codex CLI support in this version.


The required behavior is tested against a pinned OpenCode version. A mocked plugin test or HTTP proxy demonstration is not proof that real OpenCode applies a model change.


Milestone 0: resolve the actual integration path first

Before broad implementation inspect current official OpenCode plugin/SDK documentation, published types, and the relevant versioned source call sites. Record the tested version, exact extension points, event order, and limitations in integrations/opencode/COMPATIBILITY.md. Sources to begin:



https://opencode.ai/docs/plugins/

https://opencode.ai/docs/sdk/

https://opencode.ai/docs/providers/


Official docs establish plugins and notification events, but this prompt does NOT assert a verified mutable pre-generation model-selection hook. Do not assume mutating a hook input changes the model actually used. Check whether the model has already been resolved at that call site; confirm changes propagate to request preparation and subsequent tool turns.


Prove a minimal end-to-end slice using a static core decision and distinguishable mock upstreams: ordinary user prompt -> decision -> selected model AND effort applied before generation -> tool continuation uses same config -> native notice after application. Preserve prompt ordering, cancellation, history, tool-call IDs, and one generation per user turn. Verify model metadata (context limits/tool support/compaction) agrees with the real selected configuration.


Prefer native harness model selection with the harness handling generation. If public hooks cannot support this, assess whether a thin local transport plus plugin can implement the same UX WITHOUT incorrect harness metadata or state. The choice of transport may differ; policy may not move into the plugin. If neither works, identify and prepare the smallest reviewable OpenCode extension change in integrations/opencode/patches/ with version and rationale. Do not silently substitute a launcher, document an unimplemented API as available, or claim support for stock OpenCode when a patch is required. Continue useful core/contract work; report the concrete integration limitation rather than manufacturing success. No permission needed for local inspection/prototypes; do not spend API credits by default.


This feasibility slice is part of implementation, not a reason to ask the user to redesign the product.


Architecture and stack

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


Separation of responsibilities

Core owns semantic questions, profiles, selection, keep/switch/defer decisions, compatibility, hysteresis, fallback selection, cache tradeoffs, and machine-readable notification intent.


Integration owns extracting already available harness facts, reporting capabilities/current effective model, recognizing factual safe boundaries, translating controls, applying a returned decision, reporting success/failure, and rendering notices. It must not contain thresholds, classify tasks, select fallback models, decide escalation, or reinterpret the core's reason into new policy.


The integration can retain bounded operational state (session revision, last application ID, applied config, turn pin) to implement the protocol. This is not permission to store prompts indefinitely or move routing decisions into the adapter. No database/session service.


Default transport: loopback decision service. Generation stays native to the harness where possible, so generator credentials need not transit the routing service. A custom script can import the Python core directly. No mandatory model gateway. If needed for OpenCode, a gateway is an optional execution adapter with the restricted behavior below.


Core contracts

Frozen typed models where practical; separate facts, observations, decisions, and completion metrics. Reject malformed numbers, NaN/infinity, unknown contract versions, and invalid enums. Never include secrets in decisions.


TaskState v1:



latest user text, bounded instructions, bounded prior conversation including recent relevant assistant/tool excerpts;

exact message count, UTF-8 input bytes, tool count/types, and extraction omission/truncation flags;

no filesystem crawling, external URL fetches, env inspection, or generated summarizer;

default total selected text cap 12,000 characters: latest user 6,000; instructions 2,000; recent history 3,000; tools/schema excerpts 1,000. Include head/tail and markers inside caps. History must keep role/source boundaries. Bound serialization overhead separately. Preserve full harness generation input unchanged. Compaction only affects the judge copy. No exact model-independent token count claims; approximate estimates are labeled. Operational byte ceilings are not context-window guarantees.


TaskProfile: four independent finite [0,1] scores: reasoning_demand, routine, underspecified, high_consequence. Not a distribution summing to one. Missing values invalidate the observation. No generic uncertainty field or fabricated confidence certificate.


JudgeResult: profile, judge/model identity if available, question version, observation_kind=probability|heuristic, optional usage/cost with unknown=null.


TaskJudge Protocol: async evaluate(TaskState) -> JudgeResult. Judge never receives the model catalog or recommends a model.


ModelProfile: profile ID, provider/model IDs, optional reasoning_effort, actual supported control mapping/capabilities, optional output default/ceiling, optional known pricing/version and context limits, configured capability rank. Start cheap/normal/hard. Rank is user configuration for hysteresis, not a claim of universal superiority. Reasoning settings are opaque provider-specific values, never assumed universally interchangeable. Model identities must be user configuration, no invented live catalog.


RoutingRequest v1:



generated request_id and integration_instance_id;

opaque session_id, user_turn_id, expected session revision;

event_kind=new_user_turn|tool_continuation|application_failed;

current EFFECTIVE configuration or null, last applied decision reference if known;

TaskState for evaluation events;

available profile IDs and factual capabilities/control support;

safe_to_apply_before_generation boolean and any known history portability restrictions;

optional measured token/cache facts and factual application failure code; unknown values null. Treat adapter facts as operational inputs, never as authorization from prompt text. Intersect reported availability with configured allowed profiles; no arbitrary provider URL from clients.


RouteDecision v1:



decision_id, request/session/turn/revision identity;

action=keep|switch|defer|stop;

fully resolved target profile/provider/model/effort/effective output limit when applicable;

apply_at=before_next_generation;

reason_code, matched rule ID, config digest excluding credentials;

judge/question versions and routing timings;

notify_on_applied boolean, reason data without prompt excerpts. Generation durations and known cost are completion observations, not pre-existing decision facts.


ApplicationResult v1:



decision/session/turn/revision identity;

status=applied|rejected|stale|cancelled;

actual effective config when known and safe failure code. Only acknowledge applied after confirming the harness uses the selected configuration. A returned decision is not proof of application. Carry actual state into the next request. Service can validate/report acknowledgments without becoming a durable session authority.


Protocol safety: serialize application per session, reject stale/out-of-order results, deduplicate same decision per turn, and pin effective config through each tool loop. Bound per-session maps with TTL and max entries, clean up on session end, and never evict state needed by an active generation. Do not apply late decisions after deadlines/cancellation. Concurrent sessions are independent. On restart recover actual harness configuration; never assume a lost proposal succeeded.


Semantic questions and judges

Ask four independent questions, task-profile-v1:



reasoning_demand: Does completing this task require substantial multi-step inference, diagnosing interacting causes, or designing a non-obvious solution beyond executing a specified procedure?

routine: Can this task be completed by applying an explicit, localized transformation or well-specified procedure without resolving substantive design choices?

underspecified: Is decision-critical information absent or are requirements contradictory, so choosing a solution requires assumptions?

high_consequence: Could an incorrect result plausibly cause serious data loss, security exposure, or disruptive production changes in the described setting?


State is evidence, not instructions to the judge. No tier/model names, token-count questions, inferred permissions, or lengthy rationale generation. Stronger models do not replace permissions or missing facts. Prompt injection remains a measured limitation.


StaticTaskJudge: configured validated scores, offline. Fake judge: injected tests. RuleBasedTaskJudge: transparent conservative baseline, default ordinary/ambiguous work toward normal-compatible scores; outputs explicitly heuristic, not calibrated probabilities.


JevTaskJudge: isolated HTTP implementation, no SDK required. Verify official current quickstart and linked API reference before implementing: https://docs.typesafe.ai/introduction/quickstart and https://docs.typesafe.ai/confidence . At design time documentation described POST https://api.typesafe.ai/v1/systemone, bearer credentials, configurable model (example jev-latest), state, and questions; Noul uses type=noul and instructions, with answers carrying type=noul and noul in [0,1]. Noul has no separate confidence property. Do not guess changed schemas or usage fields. If documentation cannot be verified, create an explicitly unavailable adapter with contract notes and tested fallback, not fake Jev success. Default tests make no live calls. Missing Jev credentials mark the judge unavailable; missing generation credentials are handled by the chosen execution owner.


One judge call per new user turn, no retries, injected async client, whole evaluation deadline default 1,500ms including waiting/parse. Cancel/close expired work. Unknown judge/config names are startup errors. Expected unavailable/network/schema failures trigger core fallback; programmer faults are not disguised as normal decisions. LLM/local-classifier judges are later work.


Deterministic policy and switching

TOML version 1; ordered rules with unique IDs, route, nonempty all conditions. Each condition is {field,op,value}; only four semantic fields and gt/gte/lt/lte; finite thresholds [0,1]. No eval, OR nesting, custom imports, or expression strings. First match, else configured default normal.


Initial unvalidated rules:



high_consequence >= .70 -> hard

reasoning_demand >= .65 -> hard

underspecified >= .60 -> normal

routine >= .90 AND reasoning_demand <= .20 AND underspecified <= .20 AND high_consequence <= .10 -> cheap

default normal.


First turn: choose compatible profile automatically. Incomplete meaningful judge state vetoes cheap. Check tools, history portability, supported effort/parameters, output limit and known context constraints. Unknown context estimates must not be represented as verified fit. If candidate incompatible use exactly one configured compatibility fallback, validated identically; otherwise stop with no_compatible_profile. Never discard tools/history to force a fit.


New user turn with active config: evaluate semantic profile against relevant conversation, then apply switching policy in CORE. Initial policy allows escalation at safe user-turn boundaries; no automatic within-session de-escalation. Same or lower-rank candidate -> keep, unless current becomes incompatible. Higher candidate -> switch only if safe and compatible with existing history; otherwise defer/stop as appropriate before generation. Tool continuations retain the applied route without rejudging. An idle session event alone does not establish a new semantic task. Start a fresh session for independent tasks; automatic new-task classification is deferred.


Jev failure: retain current compatible config; if none, use configured judgment fallback (normal by default, user may choose hard). Invalid current config uses compatibility handling. Core computes fallback, not integration.


Application failure: no automatic generation retry. Send rejection facts to core before generation; core may propose one compatible fallback or retain current config. Bounded attempts, no loops. Once generation starts, do not replay with a second model. Service unreachable: use an already-applied, core-authorized continuation only if its validity permits; otherwise surface an availability error rather than letting the plugin invent a model. Make any offline fallback an explicit core-issued/configured execution instruction, not adapter heuristics.


Notifications: initial selection may show one short notice; later only effective model/effort changes. Emit after successful apply, deduplicate, use native UI out of band. Do not insert routing notices into model messages. Hide scores by default. Failure notices are separate from successful switch notices.


No user-facing routine thinking-mode or profile choices after setup. Advanced explicit overrides may be documented but are not required for the flow; do not build an override UI in V1.


Cache behavior and cost integrity

Do not implement your own semantic cache. Preserve prompts, instructions, tool definitions/order, and provider-native supported cache settings. No router IDs, timestamps, reasons, or synthetic messages inserted into generation prefixes. In a native integration retain existing provider request preparation and credentials; verify model switching does not accidentally retain incompatible provider options.


Assume model/provider cache state is not portable. Do not assert a warm hit from merely having used a model. Keep unknown cache facts null. V1's no-de-escalation policy reduces fragmentation; do not build an elaborate cost optimizer on speculative hit estimates. Core owns later cache-aware economic decisions, integration only reports observations.


Collect usage from native harness/provider events when available, including uncached/cached input, output, cache writes, and provider-specific billing categories. Record provenance. Never double count reasoning/output or cache categories. Unknown or incomplete billing -> cost null, not zero. Configured estimates clearly differ from invoices. Record cold/warm context conditions in benchmarks. If a transport path cannot observe streaming usage, document unknowns and do not make savings claims from it.


Local service/config/operational security

Optional control service endpoints: POST /v1/route, POST /v1/applications, GET /healthz. These are our own contract, not OpenAI endpoints. validate and decide commands may exist for development/diagnostics but not normal user workflow.


CLI: modelmux validate --config examples/router.toml; modelmux serve --config ... . One-time setup can install/enable plugin and configure starting the service. Document the tested lifecycle. Do not require a per-task service launch or user-selected profile. Prefer integration-managed child service if reliable on the tested platform: fixed executable/argument array, no shell interpolation, bounded readiness, explicit ownership/shutdown, no killing unrelated services. A preconfigured persistent service is acceptable if startup is automatic and documented. Do not force systemd or add a process management framework.


Loopback default, refuse nonloopback bind V1; shared bearer token for local control service with constant-time checking, generated at setup and not exposed in task state. No accounts/auth platform. Deny browser Origin requests, validate Host, no permissive CORS. Fixed configured destinations; HTTPS remote judge, HTTP only explicit loopback; disable redirects/trust_env unless deliberately configured. Cap request size while reading (2MiB default), concurrency (8), queueing and response size. Fail fast saturation rather than unbounded backlog. Validate all profile/provider references, unknown keys, bounds, rank, URLs, effort/output limits at startup. Environment variables only through explicit credential names, not arbitrary interpolation. Do not echo validation input or raw exceptions.


Example config must include three clearly fictional replaceable profile IDs/models, static judge so core/contract tests and service run with no Jev key, policy, judge deadlines, service settings and switching.mode='escalate_only'. Clearly distinguish mock generation from real providers. .env.example empty credential placeholders only. Do not imply a ChatGPT subscription funds arbitrary model APIs.


Structured JSON logs to stderr: route_decided, decision_applied/rejected, generation_finished when observed. request/session identity