"""Singleflight pattern for coalescing duplicate concurrent asynchronous calls."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Tuple
from services.common.telemetry import setup_logging

logger = setup_logging("mcp-gateway-singleflight")


class SingleFlightGroup:
    """Collapses concurrent calls with the same key into one execution."""

    def __init__(self) -> None:
        self._flights: Dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    async def execute(self, key: str, fn: Callable[[], Awaitable[Any]]) -> Tuple[Any, bool]:
        """Executes fn or waits for an existing in-flight call for the given key.
        
        Returns:
            (result, was_leader): was_leader is True if this invocation performed the actual call.
        """
        async with self._lock:
            if key in self._flights:
                # Follower: wait on existing leader future
                future = self._flights[key]
                logger.debug("Coalescing duplicate in-flight call via singleflight", extra={"key": key})
                was_leader = False
            else:
                # Leader: create future and register
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._flights[key] = future
                was_leader = True

        if not was_leader:
            # Wait for leader to resolve
            return await future, False

        # As leader, execute the call
        try:
            result = await fn()
            future.set_result(result)
            return result, True
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            async with self._lock:
                if key in self._flights and self._flights[key] is future:
                    del self._flights[key]


# Global singleflight group
singleflight = SingleFlightGroup()
