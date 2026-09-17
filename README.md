# modelmux

Focus on the work, not the model switcher.

modelmux is intended to select an explicitly configured model and reasoning
setting for each task. The harness-independent core will own model selection,
policy, and judge logic; separate harness integrations will provide facts and
apply the core's decisions. Jev is planned as an optional implementation
behind a replaceable judge interface.

Only the project scaffold exists today. Routing, judges, service endpoints,
and harness integrations are not implemented.

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

There are no application tests yet, so `uv run pytest` currently reports no
tests collected and exits with pytest's usual no-tests status.

The optional service dependencies can be installed with:

```sh
uv sync --locked --dev --extra service
```

The implementation roadmap is in [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md),
and the current handoff is in [docs/PROGRESS.md](docs/PROGRESS.md).
