# modelmux

Focus on the work, not the model switcher.

modelmux is intended to select an explicitly configured model and reasoning
setting for each task. The harness-independent core will own model selection,
policy, and judge logic; separate harness integrations will provide facts and
apply the core's decisions. Jev is an optional implementation behind a
replaceable judge interface.

Validated configuration loading, bounded task-state extraction, static,
rules, and Jev judges, policy evaluation, and routing decisions are
implemented. Model profiles, router references, ordered policy rules, and
judge selection are read from TOML; supplied message content is turned into a
bounded `TaskState`, evaluated by the configured judge, and resolved into a
routing decision. The local service and harness integrations are not
implemented yet.

## Configuration

Configuration is TOML. [examples/router.toml](examples/router.toml) is a
complete starting point whose provider and model identifiers are placeholders;
replace them with providers and model IDs your account can actually use.

```toml
version = 1

[router]
default = "normal"
judgment_fallback = "normal"
compatibility_fallback = "hard"
economy = "cheap"

[judge]
selection = "static"  # "static", "rules", or "jev"
deadline_ms = 1500

[judge.static.scores]
reasoning_demand = 0.20
routine = 0.90
underspecified = 0.10
high_consequence = 0.00

[profiles.normal]
provider = "example-provider"
model = "example-provider/normal-placeholder"
rank = 1
capabilities = ["text", "tools"]
```

Load and validate it with:

```python
from modelmux.config import load_config

config = load_config("examples/router.toml")
```

Validation checks syntax, types, ranges, profile references, and output limits.
It cannot verify that a provider account can access a model. Jev configuration
stores the name of an environment variable; the credential is resolved when
the judge is created, not while loading configuration.

## Task state, judges, and routing

Extraction is deterministic and harness-neutral. The caller supplies message,
instruction, and tool text; the result is a bounded `TaskState` that records
truncation and omission counts. `StaticTaskJudge` returns configured scores,
`RuleBasedTaskJudge` is a small conservative cue baseline, and `JevTaskJudge`
asks TypeSafe's System One API the four semantic questions in one request and
reports probability observations. No judge sees the model catalog, profile
IDs, or tier names, and none recommends a model. The Jev adapter and its
verified contract are documented in [docs/jev.md](docs/jev.md); it needs an
injected `httpx.AsyncClient` created with `trust_env=False` and a credential
environment variable scoped `MODELMUX_JEV_*` or `JEV_*`, and falls back through
the Router when unavailable.

`Router` evaluates one `TaskState` with an injected judge and returns a
`RouteDecision` with action `keep`, `switch`, `defer`, or `stop`. Decisions are
proposals: the caller applies the resolved target and owns the result. The
caller then reports an `ApplicationResult`, and only `status="applied"`
acknowledges that the harness uses the reported configuration. A profile is
eligible only when it is configured and the caller reports it available with
the required capabilities and any known effort, output-ceiling, and context
facts; unknown facts are not treated as verified. Before retaining a current
configuration the router validates its provider, model, effort, and effective
output limit against the configured profile and reported facts. Ineligible
candidates use one checked compatibility fallback, which may not downgrade or
replace a valid current route; a judge failure retains the current route or
uses the judgment fallback; tool continuations bypass the judge.

## Security and privacy

The `static` and `rules` judges are local and never send task content off the
machine. Selecting `jev` sends the latest user text, instructions, history, and
tool excerpts (up to 12,000 characters total) to
`https://api.typesafe.ai/v1/systemone` with the configured bearer key. Tool
output and instructions can contain secrets or PII; set
`include_instructions`, `include_history`, or `include_tool_excerpts` to `false`
in `[judge.jev]` if those categories must not leave the machine. The credential
variable must be named `MODELMUX_JEV_*` or `JEV_*`; a copied configuration
cannot point the adapter at an unrelated cloud secret.

The injected Jev client controls TLS, proxy, and timeout behavior. Create it
with hardened settings and keep ownership explicit:

```python
import httpx

client = httpx.AsyncClient(
    trust_env=False,
    follow_redirects=False,
    timeout=httpx.Timeout(5.0),
)
judge = build_judge(config.judge, client=client)
# The caller owns the client and must close it when finished.
```

The adapter rejects a client that inherits environment proxy/CA settings and
caps the judge response at 64 KiB. The Router's `deadline_ms` bounds the
evaluation. Prompt injection remains a measured limitation: an LLM judge can be
influenced by task text, and the policy trusts its scores; use a local judge for
high-consequence or secret-bearing sessions. The core also trusts the caller's
reported availability, state, safety, and history facts; a service must
authenticate the caller before treating them as operational input. Decisions
carry scores, judge identity, and a configuration digest (provenance, not a
signature) but never prompt text. Application results are proposals until the
caller reports `applied`. See [SECURITY.md](SECURITY.md) for the reporting
policy and [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) for the threat model
and remaining limitations.

## Versioned contracts

The boundary types are `RoutingRequest`, `RouteDecision`, and
`ApplicationResult`. JSON schemas are generated from those Pydantic models
into `contracts/v1/` and are checked against them by the test suite:

```sh
uv run python scripts/generate_contract_schemas.py
uv run pytest
```

Representative serialized examples live in `contracts/v1/fixtures/`. Decisions
carry correlation IDs, the action and reason, a digest of the validated
routing configuration (credential references excluded), the exact judge
observation used for routing (scores, observation kind, judge/model identity,
question version, and reported usage, or null when no judge was called), and a
`notify_on_applied` flag; they never contain prompt text or secrets.
Application results carry the decision identity, a status of `applied`,
`rejected`, `stale`, or `cancelled`, and the actual effective configuration
when known. The generated schemas also express the cross-field action/target,
notification, elapsed-time, and application-status invariants for non-Python
consumers.

```python
from modelmux.config import load_config
from modelmux.judges import build_judge
from modelmux.router import ProfileAvailability, Router, RoutingRequest
from modelmux.state import MessageRecord, extract_task_state

config = load_config("examples/router.toml")
router = Router(config, build_judge(config.judge))

state = extract_task_state([MessageRecord(role="user", text="rename the config key")])
request = RoutingRequest(
    request_id="r1",
    integration_instance_id="local-1",
    session_id="s1",
    user_turn_id="t1",
    session_revision=1,
    event_kind="new_user_turn",
    task_state=state,
    available_profiles=tuple(
        ProfileAvailability(profile_id=name, capabilities=frozenset({"text", "tools"}))
        for name in config.profiles
    ),
)
decision = await router.route(request)
```

Selected text is capped at 6,000 characters for the latest user message, 2,000
for instructions, 3,000 for recent history, and 1,000 for tool excerpts, with
fixed caps on retained records and source labels. The Python contract also
enforces the per-category sums and a 128 KiB serialized-state limit; those
aggregate and serialization checks are runtime validation that standard JSON
Schema cannot express. Extraction reads only the supplied records: it does not
read files, the environment, or URLs.

## Development

Install the locked development environment with [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked --dev
```

Run the configured checks:

```sh
uv run ruff format src tests
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest
```

`uv run pytest` runs the focused offline tests; no API keys or network access
are required.

The optional service dependencies can be installed with:

```sh
uv sync --locked --dev --extra service
```

The implementation roadmap is in [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md),
and the current handoff is in [docs/PROGRESS.md](docs/PROGRESS.md).
