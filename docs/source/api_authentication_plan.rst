API authentication follow-up plan
=================================

Status: prototype follow-up
--------------------------

The DataRegistry API currently has minimal Globus Auth protection suitable for
prototype development. All ``/datasets`` routes require a bearer token. The API
introspects that token with Globus Auth and verifies that it is active, has the
expected issuer and resource-server audience, contains one configured read
scope, and has a subject identity. Globus Auth is responsible for the identity
requirements attached to that scope. ``/health`` intentionally remains public
for liveness checks.

This document records work intentionally deferred from the prototype. It is a
planning document, not a statement that the items below are currently
implemented or a production-readiness claim. Setup details for the current
implementation are in :doc:`installation_api`.

Prototype baseline
------------------

The following behavior exists today:

* ``Authorization: Bearer <token>`` is required for ``/datasets`` routes.
* The API uses a confidential Globus Auth client to introspect each presented
  token.
* The token must be active, identify Globus Auth as its issuer, include the
  configured API audience, include the configured read scope, and have ``sub``.
* The API creates an in-memory request principal containing ``sub`` and the
  returned identity set, username, name, email, and scopes.
* Missing or unacceptable credentials receive the standard error envelope with
  HTTP 401 and ``WWW-Authenticate: Bearer``. A valid token without the required
  scope receives HTTP 403.
* The normal API suite substitutes a test principal and does not contact Globus
  Auth.

The prototype does **not** cache introspection responses, define an explicit
network-failure policy, log authentication decisions, or implement authorization
beyond one read scope.

Deferred work before a production deployment
--------------------------------------------

1. Introspection reliability and caching
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a small, bounded cache for successful introspection responses.

* Key cache entries by a one-way cryptographic digest of the token; never retain
  raw bearer tokens as cache keys, logs, metrics labels, or error details.
* Set the entry lifetime to the earlier of a configured maximum TTL and the
  token expiry time. Expired tokens must never be served from cache.
* Bound the cache by entry count and memory use. Define eviction behavior.
* Do not cache failed introspection responses for long; a short, deliberate
  negative-cache policy may be considered only after documenting its security
  and user-experience consequences.
* Define request timeouts, retry limits, and connection reuse for calls to
  Globus Auth.
* Fail closed when Globus Auth cannot be reached or returns an invalid response.
  Return a stable service error without exposing upstream response bodies,
  credentials, or bearer tokens.
* Add tests for cache hits, token-expiry capping, eviction, inactive-token
  behavior, upstream timeouts, and malformed upstream responses.

2. Authentication observability and incident operations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Establish an operational convention before the API is exposed to untrusted
networks.

* Add a request/correlation ID that can be returned to clients and recorded in
  structured logs.
* Record authentication outcome, route, status, reason category, and after
  successful validation a privacy-reviewed stable principal identifier.
* Never log bearer tokens, authorization headers, client secrets, or complete
  introspection responses.
* Define metrics for authentication success/failure categories, introspection
  latency, cache hit rate, upstream failure rate, and 401/403 responses.
* Document alert thresholds, secret rotation, compromised-client response, and
  the procedure to revoke or change the Globus scope/policy.

3. Globus Auth policy integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Identity eligibility belongs primarily in Globus Auth, but future requirements
may require server-side verification of additional policy results.

* Identify the Globus authentication policy or policies relevant to each API
  scope.
* If the API must enforce a policy result explicitly, request
  ``policy_evaluations`` during introspection and verify the configured policy
  ID's successful evaluation.
* Define behavior for a policy that is absent, unknown, or unevaluable; default
  to denial rather than treating absence as success.
* Preserve the current `GlobusPrincipal` boundary so policy-specific decisions
  are made in authorization dependencies, not database handlers.
* Test compliant, non-compliant, absent-policy, and multiple-policy responses.

4. Authorization model beyond read access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The prototype has one scope for all read endpoints. Before adding sensitive
operations, design authorization as a separate layer from token validation.

* Define scope names and resource-server semantics for read, transfer, and each
  class of write operation.
* Decide whether public metadata, namespace-specific metadata, paths, transfer
  initiation, and writes have distinct permissions.
* Decide whether authorization is based solely on scopes, Globus groups/policy
  results, dataset ownership, namespace membership, or a combination.
* Use stable Globus identity IDs (``sub``), rather than mutable usernames or
  email addresses, for persisted ownership or authorization records.
* Require an explicit authorization dependency on every new router and endpoint.
  Add route-registration tests so a new endpoint cannot accidentally bypass the
  policy.

5. Deployment security boundary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Authentication is only one part of the server security boundary.

* Terminate TLS at the ASGI server or a trusted reverse proxy; reject plain HTTP
  at the public boundary.
* Define trusted-proxy, forwarded-header, trusted-host, CORS, request-size, and
  rate-limit settings for the selected deployment platform.
* Keep Globus confidential-client credentials in a secret manager. Rotate them
  without source changes and restrict their read access to the API workload.
* Use a database account limited to the privileges required by the deployed API.
  The initial API should have read-only access to registry schemas.
* Restrict database and Globus Auth egress networking to the API deployment.
* Document backup, disaster recovery, secret rotation, and deployment rollback
  procedures.

6. Tests and release gates
~~~~~~~~~~~~~~~~~~~~~~~~~~

Expand verification beyond the current unit-style claim-validation tests.

* Test the complete dependency path with a mocked Globus SDK introspection
  client and an actual ``Authorization`` header.
* Cover malformed headers, inactive tokens, wrong issuer/audience, expired or
  malformed claim data, insufficient scope, upstream failures, and valid linked
  identities.
* Verify all protected routes reject anonymous requests and explicitly test the
  intended public health route.
* Add contract tests that generated FastAPI OpenAPI and the checked-in
  :file:`../../openapi.yaml` agree on the bearer security scheme and 401/403
  responses.
* Run these tests in CI without live Globus Auth credentials. Keep one separate,
  controlled integration environment for a periodic real Globus Auth smoke test
  if needed.

Suggested milestones
--------------------

Before the API is made reachable outside a controlled prototype environment,
complete items 1, 2, 5, and the relevant parts of 6. Complete item 3 when a
server-enforced Globus policy is introduced. Complete item 4 before adding
transfers, writes, or any data access whose permissions differ from the single
read scope.

At each milestone, update :doc:`installation_api`, the OpenAPI contract, and
this document so deployment instructions, implementation, and policy remain in
agreement.
