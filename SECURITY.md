# Security policy

## Supported versions

The `main` branch and the latest `0.1.x` release receive security fixes. Older
commits are not supported.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository
("Security" tab → "Report a vulnerability"). Do not open a public issue for a
suspected vulnerability. If private reporting is unavailable, contact the
maintainer through the repository owner's GitHub profile and ask for a private
channel.

Include:

- the affected version or commit;
- a minimal reproduction or the exact configuration and request shape;
- the impact you believe is possible;
- whether task text, tool output, or a credential was exposed.

Do not include real credentials, prompts, or customer data in a report. Use
synthetic values.

## Scope and trust model

The core library treats the following as trusted: the configuration file and
its administrator, the in-process caller/integration and the operational facts
it reports, and the injected `httpx.AsyncClient`. The following are untrusted:
task text, instructions, history, tool output, the remote judge response, the
network, and the CI dependency/action supply chain.

Relevant security properties:

- The only outbound destination is `https://api.typesafe.ai/v1/systemone`; the
  URL is fixed and redirects are disabled per request.
- The Jev credential variable must be named `MODELMUX_JEV_*` or `JEV_*`; the
  judge refuses an `httpx.AsyncClient` that inherits environment proxy/CA
  settings (`trust_env=True`).
- Jev receives up to 12,000 characters of task state. Instructions, history,
  and tool excerpts can be excluded with the `judge.jev.include_*` switches;
  the local `static`/`rules` judges never send task content off the machine.
- The config digest is provenance, not a signature. Decisions are proposals;
  the caller applies them and reports the result.
- Prompt injection can influence an LLM judge and therefore model selection.
  This is a known limitation; reports of concrete bypasses or missing
  mitigations are welcome.

The HTTP service, CLI, gateway, and harness integrations are not implemented in
this version. Reports about them should wait until the relevant code exists;
the implementation plan records the required controls (loopback bind, bearer
token, request/concurrency limits, log redaction, and session sequencing).

See [CHANGELOG.md](CHANGELOG.md) for the security-hardening changes and
compatibility notes, and [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) for the
audit and remaining limitations.

## Safe test data

The test suite is offline and uses only fake credentials. Do not replace the
fake values with real keys.
