"""Tests for TOML configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from modelmux.config import (
    ComparisonOperator,
    ConfigError,
    JudgeKind,
    OutputLimits,
    RouterConfig,
    load_config,
)

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "examples" / "router.toml"

BASE_CONFIG = """\
version = 1

[router]
default = "normal"
judgment_fallback = "normal"
compatibility_fallback = "normal"
economy = "normal"

[judge]
selection = "rules"

[profiles.normal]
provider = "example-provider"
model = "example-provider/normal-placeholder"
rank = 1
capabilities = ["text"]
"""

SECRET = "sk-live-0123456789abcdef"


def _load(tmp_path: Path, text: str) -> RouterConfig:
    path = tmp_path / "router.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def _condition(field: str = "routine", op: str = "gte", value: str = "0.5") -> str:
    return f'{{ field = "{field}", op = "{op}", value = {value} }}'


def _rule(rule_id: str, conditions: str, route: str = "normal") -> str:
    return f'[[policy.rules]]\nid = "{rule_id}"\nroute = "{route}"\nconditions = [{conditions}]\n'


def test_loads_representative_example() -> None:
    config = load_config(EXAMPLE_CONFIG)

    assert config.version == 1
    assert config.router.default == "normal"
    assert config.router.judgment_fallback == "normal"
    assert config.router.compatibility_fallback == "hard"
    assert config.router.economy == "cheap"
    assert set(config.profiles) == {"cheap", "normal", "hard"}
    assert [rule.id for rule in config.policy.rules] == [
        "high-consequence-hard",
        "reasoning-demand-hard",
        "underspecified-normal",
        "routine-cheap",
    ]
    assert config.policy.rules[0].conditions[0].op is ComparisonOperator.GTE
    assert config.judge.selection is JudgeKind.STATIC
    assert config.judge.deadline_ms == 1500
    assert config.judge.static is not None
    assert config.profiles["cheap"].output == OutputLimits(default=2048, max=4096)
    assert config.profiles["cheap"].context_limit == 32000
    assert config.profiles["hard"].reasoning_effort == "high"


@pytest.mark.parametrize(
    "text",
    [
        'unknown_key = "value"\n' + BASE_CONFIG,
        BASE_CONFIG + 'unknown_key = "value"\n',
    ],
    ids=["top-level", "profile"],
)
def test_rejects_unknown_keys(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)
    assert "unknown_key" in str(excinfo.value)


@pytest.mark.parametrize(
    "key",
    ["default", "judgment_fallback", "compatibility_fallback", "economy"],
)
def test_rejects_dangling_router_reference(tmp_path: Path, key: str) -> None:
    text = BASE_CONFIG.replace(f'{key} = "normal"', f'{key} = "missing"')

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    message = str(excinfo.value)
    assert f"router.{key}" in message
    assert "unknown profile 'missing'" in message


def test_rejects_dangling_rule_route(tmp_path: Path) -> None:
    text = BASE_CONFIG + _rule("rule-one", _condition(), route="missing")

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    message = str(excinfo.value)
    assert "policy.rules[rule-one].route" in message
    assert "unknown profile 'missing'" in message


@pytest.mark.parametrize(
    ("condition", "location"),
    [
        (_condition(field="confidence"), "conditions.0.field"),
        (_condition(op="eq"), "conditions.0.op"),
        (_condition(value="1.5"), "conditions.0.value"),
        (_condition(value="-0.1"), "conditions.0.value"),
        (_condition(value="nan"), "conditions.0.value"),
        (_condition(value="inf"), "conditions.0.value"),
    ],
)
def test_rejects_invalid_policy_conditions(
    tmp_path: Path, condition: str, location: str
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, BASE_CONFIG + _rule("rule-one", condition))
    assert location in str(excinfo.value)


def test_rejects_empty_policy_conditions(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, BASE_CONFIG + _rule("rule-one", ""))
    assert "policy.rules.0.conditions" in str(excinfo.value)


def test_rejects_duplicate_rule_ids(tmp_path: Path) -> None:
    rule = _rule("duplicate", _condition())

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, BASE_CONFIG + rule + rule)

    assert "duplicate policy rule id" in str(excinfo.value)


def test_rejects_inconsistent_output_limits(tmp_path: Path) -> None:
    text = BASE_CONFIG + "[profiles.normal.output]\ndefault = 8192\nmax = 4096\n"

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    message = str(excinfo.value)
    assert "profiles.normal.output" in message
    assert "must not exceed" in message


def test_accepts_matching_output_limits(tmp_path: Path) -> None:
    text = BASE_CONFIG + "[profiles.normal.output]\ndefault = 4096\nmax = 4096\n"
    config = _load(tmp_path, text)
    assert config.profiles["normal"].output == OutputLimits(default=4096, max=4096)


def test_rejects_unknown_judge_selection(tmp_path: Path) -> None:
    text = BASE_CONFIG.replace('selection = "rules"', 'selection = "unknown"')

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    assert "judge.selection" in str(excinfo.value)


@pytest.mark.parametrize(
    ("selection", "section"),
    [("static", "judge.static"), ("jev", "judge.jev")],
)
def test_requires_selected_judge_section(
    tmp_path: Path, selection: str, section: str
) -> None:
    text = BASE_CONFIG.replace('selection = "rules"', f'selection = "{selection}"')

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    assert section in str(excinfo.value)


def test_jev_credentials_are_names_not_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MODELMUX_TEST_JEV_API_KEY", raising=False)
    text = BASE_CONFIG.replace('selection = "rules"', 'selection = "jev"')
    text += '\n[judge.jev]\napi_key_env = "MODELMUX_TEST_JEV_API_KEY"\n'

    config = _load(tmp_path, text)

    assert config.judge.jev is not None
    assert config.judge.jev.api_key_env == "MODELMUX_TEST_JEV_API_KEY"
    assert config.judge.jev.model == "jev-latest"


def test_syntax_error_reports_location_without_content(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, "version = \n")
    assert "invalid TOML syntax at line 1" in str(excinfo.value)


@pytest.mark.parametrize(
    "text",
    [
        f'unknown_key = "{SECRET}"\n' + BASE_CONFIG,
        BASE_CONFIG + f'unknown_key = "{SECRET}"\n',
        BASE_CONFIG.replace("rank = 1", f'rank = "{SECRET}"'),
        BASE_CONFIG.replace(
            'model = "example-provider/normal-placeholder"',
            f'model = "{SECRET}',
        ),
    ],
    ids=["unknown-key", "unknown-profile-key", "wrong-type", "bad-syntax"],
)
def test_error_messages_do_not_echo_config_values(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    assert SECRET not in str(excinfo.value)
    assert excinfo.value.__suppress_context__ is True
