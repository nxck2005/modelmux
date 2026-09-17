# Jev task judge

`modelmux.judges.jev.JevTaskJudge` adapts TypeSafe's System One API to the
core `TaskJudge` contract. TypeSafe-specific shapes stay inside this adapter;
the core only sees `TaskState`, `TaskProfile`, and `JudgeResult`.

## Verified contract

Verified against the official documentation on 2026-09-18:

- <https://docs.typesafe.ai/introduction/quickstart>
- <https://docs.typesafe.ai/api>
- <https://docs.typesafe.ai/confidence>

- Endpoint: `POST https://api.typesafe.ai/v1/systemone`
- Authentication: `Authorization: Bearer <API_KEY>`, where the key comes from
  the environment variable named by `judge.jev.api_key_env`.
- Request body: `{"state": <string|object|array>, "model": "<model>",
  "questions": {"<id>": {"type": "noul", "instructions": "..."}}}`. The
  configured `model` defaults to `jev-latest`.
- Response body: `{"model": "...", "answers": {"<id>": {"type": "noul",
  "noul": <0..1>}}, "usage": {"input_tokens": <int>, "output_tokens": <int>}}`.
- Noul answers carry `type` and `noul` only. The confidence page states that
  Noul answers have no `confidence` property, so the adapter never reads one.
- Documented error statuses: 401, 422, 429, and 529.

## Behavior

- One request per evaluation with the four agreed semantic questions
  (`reasoning_demand`, `routine`, `underspecified`, `high_consequence`),
  keyed by their semantic field names.
- `TaskState` is translated to a structured `state` object with
  `latest_user`, `instructions`, `history`, and `tool_excerpts`, preserving
  each block's source label. The judge never receives the model catalog,
  profile IDs, tier names, or router metadata.
- Missing credentials, transport and HTTP failures, invalid JSON, and
  responses that do not match the documented schema raise `JudgeError`. The
  Router turns those into its normal fallback behavior; unrelated
  static/rules use never reads the Jev credential.
- Exactly one request is made. There are no retries and no separate inner
  deadline: the Router's `deadline_ms` bounds the whole evaluation and cancels
  the in-flight request. The injected `httpx.AsyncClient` is owned by the
  caller and supplies transport timeouts.
- Usage token counts are reported when present; absent or unknown values stay
  `None`. The documented response has no cost field, so cost is not reported.
- Prompts, credentials, response bodies, and raw HTTP errors are not logged or
  surfaced in error messages.

## Limitations

- Mock-transport tests do not establish Jev accuracy, latency, or cost
  savings, and no live paid calls were made.
- The adapter asks only Noul questions and expects Noul answers. A Choice or
  Score answer under a requested key is treated as a schema failure.
- The endpoint is fixed to the documented System One URL; it is not
  configurable yet.
- Only `input_tokens` and `output_tokens` usage fields are consumed.
