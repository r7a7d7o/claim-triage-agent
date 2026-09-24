"""The one fixture more than one test module needs: an app served for real on a loopback port.

Driving a deployed app through a socket rather than calling the ASGI callable in process is what
makes a test cover the parts a hop between two deployables actually crosses — serialisation, status
codes, and the headers a trace context travels in. The two helpers this builds on live in `support`,
which a test module can import directly.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
import uvicorn

from support import STARTUP_DEADLINE_SECONDS, free_port, wait_until_listening

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI


@pytest.fixture
def serve() -> Iterator[Callable[[FastAPI], str]]:
    """Serve apps for real on loopback ports, and stop every one of them afterwards."""
    served: list[tuple[uvicorn.Server, threading.Thread]] = []

    def _serve(app: FastAPI) -> str:
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        served.append((server, thread))
        wait_until_listening(port)
        return f"http://127.0.0.1:{port}"

    yield _serve

    for server, _ in served:
        server.should_exit = True
    for _, thread in served:
        thread.join(STARTUP_DEADLINE_SECONDS)
