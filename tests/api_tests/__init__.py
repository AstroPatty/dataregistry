"""Tests for the ``dataregistry_api`` server.

The API test schemas live under a dedicated namespace so runs never collide
with the end-to-end suite or with seeded dev data. The server reads its
default namespace from the environment at import time, so it has to be set
here — before ``conftest`` imports the application — rather than in a fixture.
"""

import os

#: Namespace used for the API test schemas. Deliberately distinct from the
#: ``lsst_desc`` namespace used by the end-to-end suite and the dev seed data.
TEST_NAMESPACE = "lsst_desc_api_test"

os.environ.setdefault("DATAREGISTRY_API_NAMESPACE", TEST_NAMESPACE)
