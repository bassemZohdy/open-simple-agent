# ADR-007: Enterprise identity lifecycle semantics

## Status

Accepted

## Date

2026-09-03

## Context

The built-in authorization baseline (P2.2) validates OIDC/JWT access tokens
against an issuer's JWKS, supports opaque tokens through RFC 7662
introspection, maps roles/permissions/scopes from claims, binds runtime
invocations to `tenant_id`/`tid`, and records authentication-relevant audit
events. OSA deliberately keeps no identity store of its own.

Open P2.2 work asked for explicit lifecycle semantics before any
provider-specific enterprise identity integration: provisioning and
deprovisioning expectations, disabled identities, role/group synchronization,
service-account lifecycle, permission revocation, and stale-token behavior.
The associated design question was whether enterprise authorization remains
claim-driven or requires an external policy/identity integration.

## Decision

Enterprise authorization remains **claim-driven**. The enterprise identity
provider (IdP) is the single lifecycle authority; OSA evaluates the claims it
presents on every request and never stores identity records. An external
policy engine (PDP) is not introduced; the deferred-item trigger for
revisiting this is a concrete requirement for per-request attribute
evaluation that cannot be expressed as token claims (for example dynamic
attribute-based access control evaluated against mutable external state).

The lifecycle semantics on top of that decision:

- **Provisioning and deprovisioning.** Provisioning an identity means the IdP
  begins issuing tokens carrying the agreed claims (`sub`, `tid`/tenant,
  roles/scopes); deprovisioning means it stops. OSA adds no provisioning API,
  sync job, or user store. Deprovisioned identities lose access when their
  current access token expires or is introspected as inactive.
- **Disabled identities.** For opaque tokens, RFC 7662 introspection is
  authoritative and live: `active: false` rejects the request immediately.
  For self-contained JWTs, disability is enforceable only at issuance or via
  a claim: tokens carrying an `active` claim set to `false` are rejected.
  Absence of the claim means nothing about provisioning status (the IdP
  expresses disablement by token lifetime and issuance policy, or by setting
  the claim).
- **Role and group synchronization.** Groups are resolved to roles inside the
  IdP; OSA re-reads role claims at every validation, so group changes apply
  as soon as the caller presents a freshly issued token. OSA runs no
  group-sync jobs and caches no identity state.
- **Permission revocation bound.** The effective revocation bound for JWTs is
  the access-token lifetime; OSA recommends short-lived access tokens
  (roughly 5–15 minutes) with refresh-token rotation at the IdP. Signing-key
  rotation propagates immediately when a token arrives with an unknown `kid`
  (the JWKS cache refreshes synchronously on the miss) and otherwise within
  `OSA_AUTH_JWKS_CACHE_SECONDS`.
- **Stale-token behavior.** Expired, unknown-signing-key, wrong-issuer/
  -audience, and introspection-inactive tokens fail with the stable
  `AuthenticationError` envelope (HTTP 401 semantics), never 500s. Replay and
  revocation-list checks are out of scope; the IdP owns them through token
  lifetime and introspection.
- **Service accounts.** Modeled as IdP-issued clients whose client-credentials
  tokens carry the same role/scope/tenant claims as user tokens. OSA applies
  identical validation and authorization; there is no interactive-session
  concept and no per-service-account state. Credential rotation happens at
  the IdP and surfaces here only as new tokens.
- **Permission model stability.** Route permissions remain the small stable
  set in `AuthPermission`; enterprise role→permission mapping stays
  configuration/claim territory (`OSA_AUTH_ENFORCE_PERMISSIONS`), so
  enterprises can express org-specific roles without OSA code changes.

Implementation that follows from these semantics (tracked in `TODO.md`):
rejecting tokens with an `active: false` claim in the shared validation path,
and integration/contract tests against a concrete enterprise IdP once one is
selected.

## Consequences

- OSA remains stateless with respect to identities; multi-replica deployments
  need no identity-cache invalidation because nothing is cached beyond JWKS
  material and token lifetime bounds.
- Revocation latency differs by token kind: introspected opaque tokens are
  near-immediate; JWTs are bounded by their remaining lifetime. Enterprises
  needing immediate JWT revocation must shorten lifetimes or switch to
  introspectable opaque tokens.
- Operator documentation must state the token-lifetime recommendation and the
  `active` claim convention so IdP administrators can encode disablement.
- If a future requirement needs per-request attributes beyond claims, the
  revisit trigger above fires and the policy-engine option is re-evaluated in
  a new ADR rather than grown into this one.
