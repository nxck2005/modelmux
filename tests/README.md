# Tests

Focused offline tests for the modelmux core. No API keys or network access are
required.

- `tests/test_config.py`: configuration loading and validation, including
  `examples/router.toml`.
- `tests/test_types.py`: task-profile and judge-result validation.
- `tests/test_state.py`: bounded, deterministic state extraction.
- `tests/test_judges.py`: static and rule-based judge behavior.
- `tests/test_policy.py`: ordered rule evaluation and thresholds.
- `tests/test_router.py`: eligibility, switching, judge failure, and the
  end-to-end offline scenario.
