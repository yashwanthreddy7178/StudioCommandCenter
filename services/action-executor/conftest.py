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
def _block_outbound_grafana_writes(monkeypatch):
    """Stops the suite from writing to a real Grafana stack.

    The write-back client is separate from the one the execution engine uses, so
    a test that stubs the control plane still leaves this one live. With a gateway
    running on localhost, every run of the suite filed real annotations against
    whatever stack .env points at -- six of them accumulated before this was
    noticed.

    Tests that exercise write-back replace this with their own stub, which takes
    precedence because the test body runs after the fixture. Anything else gets a
    refusal, which the caller records as a failed write exactly as it would a
    Grafana outage.
    """
    from src.grafana_writeback import grafana_writeback

    async def _refuse(*args, **kwargs):
        raise RuntimeError(
            "outbound Grafana write blocked in tests; stub "
            "grafana_writeback._http_client.post to exercise the write path"
        )

    monkeypatch.setattr(grafana_writeback._http_client, "post", _refuse)
