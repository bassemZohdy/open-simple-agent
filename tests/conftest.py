"""Root test configuration for Open Simple Agent."""

import os

# Local protocol tests must not inherit a machine-wide proxy.  The outbound
# policy explicitly permits loopback endpoints used by the test servers.
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)
os.environ.setdefault("OSA_OUTBOUND_ALLOWED_HOSTS", "localhost,127.0.0.1,::1")
