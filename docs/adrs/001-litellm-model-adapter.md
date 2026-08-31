# ADR-001: LiteLLM as the first production model adapter

## Status

Accepted

## Date

2026-08-31

## Owners

Open Simple Agent maintainers

## Context

The ADK runtime needs at least one path to a real model. Google ADK ships a
`LiteLlm` model class (`google.adk.models.lite_llm`) that bridges ADK's
`BaseLlm` interface to [LiteLLM](https://docs.litellm.ai/), which in turn
speaks to OpenAI, Anthropic, Gemini, Mistral, local OpenAI-compatible
endpoints, and many other providers through one interface. OSA's
`ModelDefinition.provider` field needs a first concrete value that maps to a
production adapter, alongside the deterministic `fake` provider used in tests.

## Decision drivers

- Broad provider coverage through a single adapter implementation.
- The adapter must plug into ADK's `Runner` so invocation, function calling,
  and session handling stay inside the ADK event loop.
- Credentials must flow through the OSA `SecretResolver` contract and never be
  persisted in definitions, responses, or logs.
- Test environments must stay offline and deterministic; the production
  adapter must not become a test dependency.

## Considered options

1. **LiteLLM via ADK's `LiteLlm` class** — one adapter covers all
   LiteLLM-supported providers; maintained upstream by the ADK team.
2. **ADK's native Google models (`GoogleLlm`)** — Gemini only; ties the first
   production path to one vendor.
3. **Direct provider SDKs (one adapter per vendor)** — maximal control but
   O(no. of providers) adapter code, keys, and error handling to maintain.

## Decision

- `provider: litellm` is the first production model provider. The adapter
  (`osa.runtimes.adk.model_adapter.LiteLlmAdapter`) builds ADK `LiteLlm`
  instances from a `ModelDefinition`, applying generation settings with
  explicit precedence: `ModelDefinition.runtime_settings` (catalog defaults)
  are overridden by `ModelRef.parameters` (per-agent overrides).
- `credential_ref` secrets are resolved with the configured `SecretResolver`
  and passed to LiteLLM as the API key; resolved values are held only by the
  model client.
- The `litellm` distribution is an **optional dependency**
  (`osa-adk-runtime[litellm]`); the runtime image installs it, test
  environments do not, and configuring `provider: litellm` without the extra
  fails fast with a deterministic error.
- Explicit non-decisions: streaming, provider-specific tuning, and a model
  registry for custom adapters are deferred; the `fake` provider remains a
  deterministic test adapter and requires explicit opt-in
  (`OSA_ALLOW_FAKE_PROVIDER=1`) in service bootstraps — it is never a
  production fallback.

## Consequences

### Positive

- Any LiteLLM-supported provider works through configuration alone.
- One adapter surface to test; provider quirks are handled upstream.
- Offline determinism is preserved: CI never needs the extra.

### Negative or trade-offs

- One more transitive dependency layer (LiteLLM) between OSA and providers,
  with version-coupling risk managed by the `google-adk>=2.0,<3.0` pin and
  the `litellm>=1.84` floor mirroring ADK's own extra.
- Providers only reachable through LiteLLM's feature subset (no
  provider-specific extensions without upstream support).

## Validation

- Unit tests cover the adapter registry, generation-setting precedence, and
  the fail-fast behavior when `litellm` is absent or a provider has no
  adapter.
- The scripted-ADK-model integration suite (`tests/integration/
  test_native_function_calling.py`) drives the same `Runner` path the
  LiteLLM model executes, with network access mocked at the model boundary.
- A live end-to-end check against a real provider runs whenever the
  `litellm` extra and credentials are present (documented in the release
  checklist).

## Follow-up

- [ ] Add a live-provider CI job (opt-in via repository secret) that runs the
      acceptance test against one real model (P3.3).
- [ ] Consider registering custom adapters through configuration once a
      second production adapter is needed.
