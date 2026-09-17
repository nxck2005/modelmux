# modelmux

Focus on the work, not the model switcher.

modelmux is intended to select an explicitly configured model and reasoning
setting for each task. The harness-independent core will own model selection,
policy, and judge logic; separate harness integrations will provide facts and
apply the core's decisions. Jev is planned as an optional implementation
behind a replaceable judge interface.

Validated configuration loading is implemented: model profiles, router profile
references, ordered policy rules, and judge selection are read from TOML.
Policy evaluation, judge calls, routing decisions, the service, and harness
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
