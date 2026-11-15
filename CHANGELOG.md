# Changelog

Notable changes are recorded here. The project is pre-1.0, so minor releases may
contain compatibility changes; the compatibility notes below call out the
changes that require a caller or configuration update.

## Unreleased — security hardening

Applies the actionable findings from [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md).
The audit remains the point-in-time analysis; this section records what changed.

### Security

- **Jev credential names are scoped.** `judge.jev.api_key_env` must now match
  `MODELMUX_JEV_*` or `JEV_*`. A copied configuration can no longer name an
  unrelated secret such as `OPENAI_API_KEY` or `AWS_SECRET_ACCESS_KEY` and have
  its value sent to TypeSafe as the bearer token.
- **Malformed credentials fail closed.** Empty, whitespace-only, and
  control-character-containing credentials raise `JudgeError` before any
  request is sent. The key is stored as `SecretStr`, so generic object dumps and
  reprs do not expose it.
- **Jev transport is hardened.** `JevTaskJudge` rejects an
  `httpx.AsyncClient` whose `trust_env` is true. Environment proxy and
  CA-bundle settings can otherwise intercept the key and task content. The
  README and `docs/jev.md` show a client with `trust_env=False`,
  `follow_redirects=False`, and a finite timeout.
- **Jev responses are bounded.** The adapter streams the response, rejects
  anything over 64 KiB (including decompressed bytes), and bounds the response
  `model` identifier to 256 characters. Redirects remain disabled per request.
- **Identifiers are bounded and sanitised.** `Identifier` is limited to 256
  characters and rejects `Cc`/`Cf` control and bidi characters. `SourceLabel`
  keeps its 32-character limit and rejects the same characters. This bounds
  decisions/logs and reduces control-character log/UI spoofing.
- **Input records are bounded.** `MessageRecord.text` and `ToolRecord.text` are
  limited to 1,000,000 characters, so `input_bytes` cannot be made to encode an
  unbounded record. The existing task-state category, record, and 128 KiB
  serialized caps are unchanged.
- **Configuration and request collections are bounded.** `deadline_ms` is
  limited to 600,000 (10 minutes); output-token values are limited to
  1,000,000; a configuration is limited to 1 MiB, 128 profiles, and 256 policy
  rules. `available_profiles` and `required_capabilities` are limited to 64
  entries, and each profile’s `capabilities`/`reasoning_efforts` to 64.
- **Boundary booleans are strict.** `safe_to_apply_before_generation`,
  `history_portable`, `recovery_used`, `notify_on_applied`,
  `BoundedText.truncated`, and `BoundedText.source_truncated` no longer accept
  `0`/`1`, `"yes"`/`"no"`, or `"on"`/`"off"`; send real booleans.
- **Configuration loading fails safely.** Invalid UTF-8, NUL-byte paths,
  oversized files, and invalid TOML/validation all become `ConfigError` instead
  of leaking a raw `UnicodeDecodeError`, `ValueError`, or parser exception.
- **Validation errors can be logged safely.** Added
  `modelmux.config.validation_problems()`, which renders only `loc`/`type`/`msg`
  and never the Pydantic `input` value. Use it for request/state validation at
  a service boundary; do not log `ValidationError.errors()`/`.json()` raw.
- **Judge wiring is checked.** `Router` requires the injected judge’s
  `judge_id` to match `config.judge.selection` (`static`, `rules`, or `jev`).
  A static configuration can no longer silently use a remote judge.
- **Configuration is snapshotted.** `Router` deep-copies the validated
  configuration at construction. Mutating the caller’s `RouterConfig` afterwards
  no longer changes routing or leaves the cached `config_digest` stale. Reload
  by constructing a new `Router`.
- **CI supply chain is hardened.** GitHub Actions are pinned to commit SHAs,
  the workflow has `permissions: contents: read`, checkout does not persist
  credentials, the job has a timeout, and Dependabot tracks the action pins.
- **Repository hygiene.** Added `SECURITY.md` with the reporting policy and
  trust model, `.env.example` with an empty scoped credential placeholder, and
  ignore patterns for `*.pem`, `*.key`, `*.p12`, `credentials.json`, and
  `.netrc`.

### Added

- `judge.jev.include_instructions`, `include_history`, and
  `include_tool_excerpts` (all default true) control which task-state categories
  are sent to the remote judge. The latest user text is always included.
- `modelmux.config.validation_problems()` for safe validation-error rendering.
- `SECURITY.md`, `.env.example`, and `.github/dependabot.yml`.
- A “Security and privacy” README section and a “Privacy and security” section
  in `docs/jev.md`.

### Compatibility notes

- Rename the Jev credential variable to a `MODELMUX_JEV_*` or `JEV_*` name.
- Create the Jev client with `trust_env=False` (the constructor now rejects a
  client that inherits the environment).
- Custom `TaskJudge` implementations must expose a `judge_id` matching the
  configured selection.
- Identifiers longer than 256 characters, and identifiers containing control or
  bidi characters, are rejected. Use opaque, short IDs.
- Single input records longer than 1,000,000 characters, `deadline_ms` above
  600,000, output-token values above 1,000,000, configurations above 1 MiB,
  more than 128 profiles, or more than 256 policy rules are rejected.
- `available_profiles` and `required_capabilities` arrays are capped at 64
  entries; profile capability/effort sets are capped at 64.
- Boundary booleans must be JSON/TOML booleans, not numeric or string
  equivalents.
- Jev responses above 64 KiB, or with a `model` identifier above 256
  characters, are rejected and the Router uses its normal fallback.
- If a service applied a configuration change by mutating an existing
  `RouterConfig`, it must construct a new `Router` instead.

### Contract schemas

Regenerated `contracts/v1/*.schema.json` to include the new identifier
`maxLength`, collection `maxItems`, and output-token `maximum` constraints and
the updated model descriptions. The fixtures remain valid.

### Verification

- `uv run pytest -q`: 228 passed, including new regressions for credential
  scoping, malformed keys, `trust_env`, response size, oversized model IDs,
  privacy switches, identifier/record bounds, strict booleans, config error
  mapping, safe validation errors, judge identity, config snapshots, and schema
  drift.
- `uv run ruff format --check src tests scripts`, `uv run ruff check src tests
  scripts`, and `uv run mypy` pass.
- Contract schemas regenerated from the authoritative models; the drift test
  passes.
- Offline only; no live Jev call.

### Deferred

- Prompt injection remains an inherent limitation of an LLM judge; it is now
  documented, but no deterministic override is added.
- Caller-supplied availability/state/safety facts remain trusted in-process;
  the service milestone must authenticate the caller and preferably extract
  task state server-side.
- Automatic prompt/tool-output redaction is not implemented; the `include_*`
  switches and documentation are the current mitigation.
- TLS verification and timeout values remain caller-owned (documented).
- The boundary contract version field and service authentication/sequencing
  are v2/service work.
- Python dependency vulnerability audit automation remains deferred; Dependabot
  currently covers GitHub Actions only.
