"""FastAPI application entrypoint for mcp-gateway."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from src.config import settings
from src.allowlist import ToolAllowlistError, validate_tool_allowed
from src.rewriter import rewrite_tool_parameters
from src.cache import cache
from src.singleflight import singleflight
from src.ratelimit import rate_limiter
from src.mcp_client import mcp_client
from services.common.models import ToolCallLog
from services.common.telemetry import setup_logging

logger = setup_logging("mcp-gateway")


class MCPCallRequest(BaseModel):
    """Payload for invoking an MCP tool through the gateway."""
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    tenant_id: str
    run_id: Optional[str] = None


class MCPCallResponse(BaseModel):
    """Response payload returned by the gateway."""
    tool_name: str
    result: Any
    latency_ms: float
    cache_hit: bool
    is_stale: bool = False
    tenant_id: str
    run_id: Optional[str] = None


# In-memory structured call logs and metrics
call_logs: List[ToolCallLog] = []
total_calls_count = 0
cache_hits_count = 0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initializes cache and rate limiters on startup."""
    await cache.initialize()
    yield
    await cache.close()
    await mcp_client.close()


app = FastAPI(
    title="Studio Production Commander - MCP Gateway",
    description="High-concurrency allowlist and caching gateway for Grafana MCP",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def healthz() -> Dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/readyz", status_code=status.HTTP_200_OK)
async def readyz() -> Dict[str, Any]:
    return {
        "ready": True,
        "service": settings.service_name,
        "qps_limit": settings.mcp_global_qps_limit,
    }


@app.post("/call", response_model=MCPCallResponse)
async def call_mcp_tool(req: MCPCallRequest) -> MCPCallResponse:
    """Executes an allowlisted MCP tool through the caching and deduplication pipeline."""
    global total_calls_count, cache_hits_count
    total_calls_count += 1
    start_time = time.time()

    # Step 1: Strict allowlist enforcement (compliance boundary)
    try:
        validate_tool_allowed(req.tool_name)
    except ToolAllowlistError as exc:
        logger.warning("Allowlist rejection", extra={"tool": exc.tool_name, "tenant": req.tenant_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    # Step 2: Server-side tenant matcher injection
    rewritten_params = rewrite_tool_parameters(req.tool_name, req.parameters, req.tenant_id)

    # Step 3: Quantize parameters to 15s bucket
    quantized_params, time_bucket = cache.quantize_params(rewritten_params)

    # Step 4: Content-addressed cache lookup
    cache_key = cache.generate_cache_key(req.tool_name, quantized_params, req.tenant_id, time_bucket)
    cached_payload, is_hit, is_stale = await cache.get(cache_key)

    if is_hit and not is_stale:
        cache_hits_count += 1
        latency_ms = round((time.time() - start_time) * 1000.0, 2)
        log_entry = ToolCallLog(
            tool_name=req.tool_name,
            parameters=rewritten_params,
            latency_ms=latency_ms,
            cache_hit=True,
            is_stale=False,
            tenant_id=req.tenant_id,
            run_id=req.run_id,
        )
        call_logs.append(log_entry)
        return MCPCallResponse(
            tool_name=req.tool_name,
            result=cached_payload,
            latency_ms=latency_ms,
            cache_hit=True,
            is_stale=False,
            tenant_id=req.tenant_id,
            run_id=req.run_id,
        )

    # Step 5 & 6: Singleflight deduplication + Token bucket rate limiting
    async def _fetch_upstream() -> Any:
        acquired = await rate_limiter.acquire(timeout_sec=5.0)
        if not acquired:
            if cached_payload is not None:
                # Degraded fallback: serve stale cache
                logger.info("Serving stale cache under rate limit pressure", extra={"tool": req.tool_name})
                return cached_payload
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Upstream Grafana MCP rate limit ceiling reached. No cached data available.",
            )

        # Call upstream MCP
        result = await mcp_client.call_upstream_mcp(req.tool_name, rewritten_params, req.tenant_id)
        # Store in cache with appropriate TTL
        ttl = cache.get_ttl_for_tool(req.tool_name)
        await cache.set(cache_key, result, ttl)
        return result

    try:
        res_data, was_leader = await singleflight.execute(cache_key, _fetch_upstream)
    except HTTPException:
        raise
    except Exception as exc:
        if cached_payload is not None:
            # Degraded fallback on 5xx error
            logger.warning("Upstream failure, serving stale cache", extra={"error": str(exc)})
            res_data = cached_payload
            is_stale = True
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Grafana MCP upstream failed: {str(exc)}",
            )

    latency_ms = round((time.time() - start_time) * 1000.0, 2)
    log_entry = ToolCallLog(
        tool_name=req.tool_name,
        parameters=rewritten_params,
        latency_ms=latency_ms,
        cache_hit=False,
        is_stale=is_stale,
        tenant_id=req.tenant_id,
        run_id=req.run_id,
    )
    call_logs.append(log_entry)

    return MCPCallResponse(
        tool_name=req.tool_name,
        result=res_data,
        latency_ms=latency_ms,
        cache_hit=False,
        is_stale=is_stale,
        tenant_id=req.tenant_id,
        run_id=req.run_id,
    )


@app.get("/logs", response_model=List[ToolCallLog])
async def get_logs(tenant_id: Optional[str] = None, run_id: Optional[str] = None) -> List[ToolCallLog]:
    """Returns structured call logs for compliance audit and UI ledger."""
    filtered = call_logs
    if tenant_id:
        filtered = [l for l in filtered if l.tenant_id == tenant_id]
    if run_id:
        filtered = [l for l in filtered if l.run_id == run_id]
    return filtered[-100:]


@app.get("/stats", response_model=Dict[str, Any])
async def get_stats() -> Dict[str, Any]:
    """Returns gateway telemetry and cache hit ratio statistics."""
    hit_ratio = (cache_hits_count / total_calls_count * 100.0) if total_calls_count > 0 else 0.0
    return {
        "total_calls": total_calls_count,
        "cache_hits": cache_hits_count,
        "cache_hit_ratio_pct": round(hit_ratio, 1),
        "active_singleflights": len(singleflight._flights),
        "rate_limiter_tokens": round(rate_limiter.tokens, 2),
    }
