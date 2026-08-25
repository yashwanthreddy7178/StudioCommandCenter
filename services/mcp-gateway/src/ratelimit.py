"""Global token bucket rate limiter for Grafana MCP traffic."""
from __future__ import annotations

import asyncio
import time
from typing import Optional
from src.config import settings
from services.common.telemetry import setup_logging

logger = setup_logging("mcp-gateway-ratelimit")


class TokenBucketRateLimiter:
    """Enforces global QPS ceiling to protect upstream Grafana Cloud quota."""

    def __init__(self, qps_limit: int = 25) -> None:
        self.qps_limit = qps_limit
        self.capacity = float(qps_limit)
        self.tokens = float(qps_limit)
        self.last_refill = time.time()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.qps_limit)
        self.last_refill = now

    async def acquire(self, timeout_sec: float = 5.0) -> bool:
        """Attempts to acquire a rate limit token within the timeout window."""
        start_time = time.time()

        while True:
            async with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True

            # If budget exceeded, check timeout
            if time.time() - start_time >= timeout_sec:
                logger.warning("Token bucket rate limit timeout exceeded", extra={"qps_limit": self.qps_limit})
                return False

            # Wait a small fraction before retrying
            await asyncio.sleep(0.05)


# Global rate limiter instance
rate_limiter = TokenBucketRateLimiter(qps_limit=settings.mcp_global_qps_limit)
