import sys
from pathlib import Path

service_dir = Path(__file__).resolve().parent
workspace_root = service_dir.parent.parent

if str(service_dir) not in sys.path:
    sys.path.insert(0, str(service_dir))
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))


import pytest


@pytest.fixture(autouse=True)
def isolated_lease_pool():
    """Gives every test a clean tenant pool.

    `lease_manager` is a module-level singleton shared by the app and every test
    that drives it over HTTP, and the tests mutate it: one fills all 24 worlds to
    reach observer mode, another leases a specific tenant and asserts on it. That
    made the suite order-dependent -- a test acquiring a lease after the
    pool-filling one silently gets an observer instead of a writable world, and
    assertions about a specific tenant depend on who ran first.

    Nothing here reaches into the app's own behaviour; it only resets the state
    between tests so each starts from an empty pool.
    """
    from src.lease import lease_manager
    from src.quota import run_quota

    def reset():
        lease_manager._leases.clear()
        # The run quota is module-level state too, and counts every run these
        # tests create. Left alone it would eventually refuse them, and the
        # failure would land on whichever test happened to run last.
        run_quota._global.clear()
        run_quota._sessions.clear()

    reset()
    yield
    reset()
