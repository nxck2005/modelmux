"""Tests for TOML configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from modelmux.config import (
    ComparisonOperator,
    ConfigError,
    JudgeKind,
    OutputLimits,
    RouterConfig,
    load_config,
    validation_problems,
)
from modelmux.types import TaskState

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

SECRET = "test-secret-not-real"


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
    monkeypatch.delenv("MODELMUX_JEV_TEST_KEY", raising=False)
    text = BASE_CONFIG.replace('selection = "rules"', 'selection = "jev"')
    text += '\n[judge.jev]\napi_key_env = "MODELMUX_JEV_TEST_KEY"\n'

    config = _load(tmp_path, text)

    assert config.judge.jev is not None
    assert config.judge.jev.api_key_env == "MODELMUX_JEV_TEST_KEY"
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


def test_rejects_unscoped_jev_credential_name(tmp_path: Path) -> None:
    text = BASE_CONFIG.replace('selection = "rules"', 'selection = "jev"')
    text += '\n[judge.jev]\napi_key_env = "OPENAI_API_KEY"\n'

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    assert "judge.jev.api_key_env" in str(excinfo.value)


def test_rejects_deadline_above_maximum(tmp_path: Path) -> None:
    text = BASE_CONFIG.replace(
        'selection = "rules"', 'selection = "rules"\ndeadline_ms = 600001'
    )

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, text)

    assert "judge.deadline_ms" in str(excinfo.value)


def test_rejects_too_many_profiles() -> None:
    data = {
        "version": 1,
        "router": {
            "default": "p0",
            "judgment_fallback": "p0",
            "compatibility_fallback": "p0",
            "economy": "p0",
        },
        "judge": {"selection": "rules"},
        "profiles": {
            f"p{index}": {
                "provider": "provider",
                "model": "model",
                "rank": 0,
                "capabilities": [],
            }
            for index in range(129)
        },
    }

    with pytest.raises(ValidationError):
        RouterConfig.model_validate(data)


def test_rejects_too_many_policy_rules() -> None:
    data = {
        "version": 1,
        "router": {
            "default": "normal",
            "judgment_fallback": "normal",
            "compatibility_fallback": "normal",
            "economy": "normal",
        },
        "judge": {"selection": "rules"},
        "profiles": {
            "normal": {
                "provider": "provider",
                "model": "model",
                "rank": 0,
                "capabilities": [],
            }
        },
        "policy": {
            "rules": [
                {
                    "id": f"rule-{index}",
                    "route": "normal",
                    "conditions": [{"field": "routine", "op": "gte", "value": 0.0}],
                }
                for index in range(257)
            ]
        },
    }

    with pytest.raises(ValidationError):
        RouterConfig.model_validate(data)


def test_invalid_utf8_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "router.toml"
    path.write_bytes(b'version = 1\n[router]\ndefault = "\xff\xfe"\n')

    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config(path)


def test_nul_path_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="invalid configuration path"):
        load_config(Path("bad\x00path.toml"))


def test_rejects_oversized_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("modelmux.config.MAX_CONFIG_BYTES", 10)

    with pytest.raises(ConfigError, match="exceeds 10 bytes"):
        _load(tmp_path, BASE_CONFIG)


def test_validation_problems_do_not_echo_input() -> None:
    payload = {
        "latest_user": None,
        "instructions": [],
        "history": [
            {
                "source": "user",
                "source_truncated": False,
                "text": SECRET * 200,
                "truncated": False,
                "omitted_chars": 0,
            }
        ],
        "tool_excerpts": [],
        "instructions_omitted": 0,
        "history_omitted": 0,
        "tools_omitted": 0,
        "message_count": 1,
        "input_bytes": 1,
        "tool_count": 0,
        "tool_names": [],
        "tool_names_omitted": 0,
        "tool_names_truncated": 0,
    }

    with pytest.raises(ValidationError) as excinfo:
        TaskState.model_validate(payload)

    problems = " ".join(validation_problems(excinfo.value))
    assert SECRET not in problems
    assert "history text exceeds" in problems
