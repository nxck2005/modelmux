# modelmux

Focus on the work, not the model switcher.

modelmux is intended to select an explicitly configured model and reasoning
setting for each task. The harness-independent core will own model selection,
policy, and judge logic; separate harness integrations will provide facts and
apply the core's decisions. Jev is planned as an optional implementation
behind a replaceable judge interface.

Validated configuration loading, bounded task-state extraction, and offline
judges are implemented. Model profiles, router references, ordered policy
rules, and judge selection are read from TOML; supplied message content is
turned into a bounded `TaskState` and evaluated by a static or rule-based
judge. Policy evaluation, routing decisions, the service, and harness
integrations are not implemented yet.

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

## Task state and judges

Extraction is deterministic and harness-neutral. The caller supplies message,
instruction, and tool text; the result is a bounded `TaskState` that records
truncation and omission counts. `StaticTaskJudge` returns configured scores and
`RuleBasedTaskJudge` is a small conservative cue baseline. Both report
heuristic observations, never see the model catalog, and never recommend a
model.

```python
from modelmux.judges.rules import RuleBasedTaskJudge
from modelmux.state import MessageRecord, extract_task_state

state = extract_task_state([MessageRecord(role="user", text="rename the config key")])
result = await RuleBasedTaskJudge().evaluate(state)
```

Selected text is capped at 6,000 characters for the latest user message, 2,000
for instructions, 3,000 for recent history, and 1,000 for tool excerpts.
Extraction reads only the supplied records: it does not read files, the
environment, or URLs.

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
