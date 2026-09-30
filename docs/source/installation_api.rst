API server setup
================

This guide configures the read-only DataRegistry HTTP API. The application is a
FastAPI ASGI application backed by PostgreSQL. All ``/datasets`` endpoints
require a Globus Auth bearer token with the DataRegistry read scope. The
``/health`` endpoint is deliberately public so that load balancers and
orchestrators can perform liveness checks.

The checked-in API contract is :file:`../../openapi.yaml`. The currently
implemented routes are ``POST /datasets/query`` and ``GET /health``; the
published OpenAPI document also contains planned routes which are not yet
served. Do not expose the application publicly until the deployment has the
Globus Auth configuration described below.

Prerequisites
-------------

* Python 3.10 or newer.
* PostgreSQL reachable from the API process. The application uses the
  ``psycopg2`` SQLAlchemy driver.
* A Globus Auth resource server and one read scope for this API.
* A Globus Auth confidential client that is allowed to introspect tokens for
  that resource server. Store its client secret in the deployment secret store.
* An ASGI server, such as `Uvicorn <https://www.uvicorn.org/>`__. Uvicorn is a
  deployment dependency and is not currently bundled by the project's ``api``
  dependency group.

For local Python-environment setup, see :doc:`installation_locally`. The
repository's :file:`../../dev/README.md` contains development database
instructions.

Install the application
-----------------------

From a source checkout, create and activate a virtual environment, then install
the API dependencies and an ASGI server:

.. code-block:: bash

   uv sync --group api
   uv pip install "uvicorn[standard]"

Alternatively, with pip, install the project and the API dependencies using the
project's supported dependency-group workflow, then install Uvicorn:

.. code-block:: bash

   python -m pip install .
   python -m pip install --group api
   python -m pip install "uvicorn[standard]"

The application object is ``dataregistry_api.app:APP``.

Globus Auth configuration
-------------------------

Create or identify a Globus Auth resource server for DataRegistry. Define one
scope for the current read-only API, for example a scope named ``read``. Record
the exact resource-server audience and complete scope string returned by Globus
Auth; do not infer either value from the native-app client ID used by the
repository's transfer tooling.

Create a **confidential** Globus Auth client for the API server. The server uses
this client's ID and secret only to call Globus Auth's token-introspection
endpoint. It does not use this credential on behalf of an API caller and must
not send the credential to clients.

Configure the Globus Auth policy for the read scope to impose any identity
requirements. Globus Auth is the authority that decides whether the caller has
an acceptable identity. The API verifies that the resulting access token is
active, comes from the expected issuer, has the intended audience, and carries
the configured read scope. Additional server-side policy checks can be added in
the future without changing this initial scope model.

Required environment
--------------------

Supply configuration as environment variables, or inject the equivalent values
from the deployment platform's secret/configuration system. All settings use the
``DATAREGISTRY_API_`` prefix.

Database settings
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 38 18 44

   * - Variable
     - Default
     - Description
   * - ``DATAREGISTRY_API_DATABASE_HOST``
     - none (required)
     - PostgreSQL hostname or IP address.
   * - ``DATAREGISTRY_API_DATABASE_NAME``
     - ``dataregistry``
     - Database name.
   * - ``DATAREGISTRY_API_DATABASE_PORT``
     - ``5432``
     - PostgreSQL port.
   * - ``DATAREGISTRY_API_DATABASE_USERNAME``
     - unset
     - Database username.
   * - ``DATAREGISTRY_API_DATABASE_PASSWORD``
     - unset
     - Database password. Set this only with a username and keep it in secret
       storage.
   * - ``DATAREGISTRY_API_NAMESPACE``
     - ``lsst_desc``
     - Default registry namespace. It identifies the
       ``<namespace>_working`` and ``<namespace>_production`` schemas.

Optional database-pool controls are ``DATAREGISTRY_API_POOL_SIZE`` (default
``5``), ``DATAREGISTRY_API_MAX_OVERFLOW`` (``10``),
``DATAREGISTRY_API_POOL_TIMEOUT`` (``5`` seconds), and
``DATAREGISTRY_API_POOL_RECYCLE`` (``1800`` seconds).

Globus Auth settings
~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 38 18 44

   * - Variable
     - Default
     - Description
   * - ``DATAREGISTRY_API_GLOBUS_AUTH_CLIENT_ID``
     - none (required)
     - Confidential client ID used by the API to introspect access tokens.
   * - ``DATAREGISTRY_API_GLOBUS_AUTH_CLIENT_SECRET``
     - none (required)
     - Confidential client secret. Store it only in secret management.
   * - ``DATAREGISTRY_API_GLOBUS_AUTH_EXPECTED_AUDIENCE``
     - none (required)
     - Resource-server audience which must occur in a token's ``aud`` claim.
   * - ``DATAREGISTRY_API_GLOBUS_AUTH_REQUIRED_SCOPE``
     - none (required)
     - Exact initial read scope which must occur in the token's space-separated
       ``scope`` claim.
   * - ``DATAREGISTRY_API_GLOBUS_AUTH_ISSUER``
     - ``https://auth.globus.org``
     - Expected token issuer.

At request time, the API calls Globus Auth token introspection with the presented
bearer token and requests ``identity_set``. It rejects inactive tokens, non-
Bearer token types, issuer or audience mismatches, tokens without the read
scope, and tokens without a subject identity. An absent or invalid credential
gets a ``401`` response with ``WWW-Authenticate: Bearer``; a valid token without
the read scope gets ``403``.

Example development configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following is illustrative only. Replace all Globus values with values from
your own registration, and do not put a real client secret in a shell history,
repository file, image, or command line in a shared environment.

.. code-block:: bash

   export DATAREGISTRY_API_DATABASE_HOST=localhost
   export DATAREGISTRY_API_DATABASE_NAME=desc_data_registry
   export DATAREGISTRY_API_DATABASE_PORT=5432
   export DATAREGISTRY_API_DATABASE_USERNAME=postgres
   export DATAREGISTRY_API_DATABASE_PASSWORD=postgres
   export DATAREGISTRY_API_NAMESPACE=lsst_desc

   export DATAREGISTRY_API_GLOBUS_AUTH_CLIENT_ID='<confidential-client-uuid>'
   export DATAREGISTRY_API_GLOBUS_AUTH_CLIENT_SECRET='<secret-from-secret-store>'
   export DATAREGISTRY_API_GLOBUS_AUTH_EXPECTED_AUDIENCE='<resource-server-audience>'
   export DATAREGISTRY_API_GLOBUS_AUTH_REQUIRED_SCOPE='<dataregistry-read-scope>'

Run the server
--------------

Run one development worker with:

.. code-block:: bash

   uv run uvicorn dataregistry_api.app:APP --host 127.0.0.1 --port 8000

Check the unauthenticated liveness endpoint:

.. code-block:: bash

   curl http://127.0.0.1:8000/health

   # {"status":"healthy"}

Call a protected endpoint with an access token acquired for the configured
DataRegistry read scope:

.. code-block:: bash

   curl \
     --header "Authorization: Bearer $DATAREGISTRY_ACCESS_TOKEN" \
     --header "Content-Type: application/json" \
     --data '{}' \
     http://127.0.0.1:8000/datasets/query

The API server does not obtain, refresh, or persist user access tokens. Token
acquisition is the responsibility of the client application.

Production considerations
-------------------------

Terminate TLS at a trusted reverse proxy or configure the ASGI server for TLS;
never expose bearer tokens over plain HTTP. Restrict database network access to
the API service identity and grant the API database account only the privileges
required for read-only endpoints. Configure proxy trust, host validation,
request-size limits, rate limits, logging, and monitoring in the selected
deployment platform—these controls are not currently configured by the FastAPI
application itself.

Do not log ``Authorization`` headers, raw access tokens, or the confidential
client secret. A failure to reach Globus Auth must be treated as an
authentication failure rather than granting access. The current implementation
introspects each protected request; deploy it only where the API can reach
Globus Auth.

Testing
-------

The API test suite uses mocked authentication and a real PostgreSQL database;
it does not contact Globus Auth. Start the local database and run:

.. code-block:: bash

   ./dev/dev-db.sh up --no-seed
   uv run --group api-test pytest -v tests/api_tests

See :file:`../../tests/api_tests/README.md` for database-test details.
