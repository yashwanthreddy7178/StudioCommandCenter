"""Content-addressed caching layer with 15-second time quantization."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple
from src.config import settings
from services.common.telemetry import setup_logging

logger = setup_logging("mcp-gateway-cache")


class QuantizedCache:
    """Manages content-addressed caching with time quantization and stale fallback."""

    def __init__(self) -> None:
        self._memory_cache: Dict[str, Tuple[float, float, Any]] = {} # key -> (created_at, ttl, data)
        self._redis_client: Optional[Any] = None

    async def initialize(self) -> None:
        """Attempts to connect to Redis if configured."""
        if settings.redis_enabled:
            try:
                import redis.asyncio as aioredis
                self._redis_client = aioredis.Redis(
                    host=settings.redis_host,
                    port=settings.redis_port,
                    password=settings.redis_password,
                    decode_responses=True,
                )
                await self._redis_client.ping()
                logger.info("Connected to Redis cache", extra={"host": settings.redis_host})
            except Exception as exc:
                logger.warning("Redis connection failed, using in-memory cache", extra={"error": str(exc)})
                self._redis_client = None

    def quantize_params(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """Snaps time parameters in queries to 15-second quantization buckets."""
        quantized = params.copy()
        bucket_size = settings.quantization_bucket_seconds
        current_time = time.time()
        time_bucket = int(current_time // bucket_size) * bucket_size

        # Snap common time keys if present
        for key in ["start", "end", "time", "since", "until"]:
            if key in quantized and isinstance(quantized[key], (int, float)):
                val = float(quantized[key])
                quantized[key] = int(val // bucket_size) * bucket_size

        return quantized, time_bucket

    def generate_cache_key(self, tool_name: str, params: Dict[str, Any], tenant_id: str, time_bucket: int) -> str:
        """Computes content-addressed sha256 hash key."""
        normalized_params_str = json.dumps(params, sort_keys=True)
        raw_key = f"{tool_name}:{tenant_id}:{time_bucket}:{normalized_params_str}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get_ttl_for_tool(self, tool_name: str) -> int:
        """Returns 300s TTL for metadata/label queries, 20s TTL for telemetry range queries."""
        if tool_name.startswith("list_") or tool_name.startswith("get_"):
            return settings.metadata_cache_ttl_seconds
        return settings.range_cache_ttl_seconds

    async def get(self, cache_key: str) -> Tuple[Optional[Any], bool, bool]:
        """Retrieves cached item.
        
        Returns:
            (payload, cache_hit, is_stale)
        """
        now = time.time()

        # Check Redis if available
        if self._redis_client:
            try:
                val = await self._redis_client.get(f"mcp:{cache_key}")
                if val:
                    data = json.loads(val)
                    return data, True, False
            except Exception as exc:
                logger.warning("Redis get failed", extra={"error": str(exc)})

        # Check in-memory store
        if cache_key in self._memory_cache:
            created_at, ttl, payload = self._memory_cache[cache_key]
            age = now - created_at

            if age <= ttl:
                # Fresh hit
                return payload, True, False
            elif age <= settings.stale_cache_max_seconds:
                # Stale hit (can be served during degradation)
                return payload, True, True
            else:
                # Expired beyond stale tolerance
                del self._memory_cache[cache_key]

        return None, False, False

    async def set(self, cache_key: str, payload: Any, ttl: int) -> None:
        """Stores item in cache with specified TTL."""
        now = time.time()
        self._memory_cache[cache_key] = (now, float(ttl), payload)

        if self._redis_client:
            try:
                serialized = json.dumps(payload)
                await self._redis_client.setex(f"mcp:{cache_key}", ttl, serialized)
            except Exception as exc:
                logger.warning("Redis setex failed", extra={"error": str(exc)})

    async def close(self) -> None:
        """Closes Redis client connection if active."""
        if self._redis_client:
            await self._redis_client.aclose()


# Global cache instance
cache = QuantizedCache()
