"""FastAPI application entrypoint for mcp-gateway."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from src.config import settings
from src.allowlist import (
    DATASOURCE_INJECTED_TOOLS,
    ToolAllowlistError,
    validate_tool_allowed,
    validate_write_tool_allowed,
)
from src.rewriter import rewrite_tool_parameters
from src.cache import cache
from src.singleflight import singleflight
from src.ratelimit import rate_limiter
from src.mcp_client import MCPUnavailableError, mcp_client
from services.common.models import ToolCallLog
from services.common.telemetry import setup_logging

# Applied before any client is constructed: on a network that inspects TLS the
# default certifi bundle cannot verify the served certificate, and every
# outbound call fails. No-op in a container.
from services.common.tls import enable_system_trust_store

enable_system_trust_store()


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


class MCPWriteRequest(BaseModel):
    """Payload for a post-approval write back into Grafana."""
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    tenant_id: str
    run_id: Optional[str] = None
    # Required, and recorded on every write. A write with no approval behind it
    # has no business reaching Grafana, and the audit trail has to be able to name
    # the approval that authorised each one.
    approval_id: str


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
    allow_credentials=False,
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

    # Step 2b: Server-side datasource resolution. Every Grafana MCP query tool
    # requires a datasourceUid; resolving it here keeps datasource selection off
    # the model's surface, so a hallucinated or borrowed UID is not reachable.
    ds_type = DATASOURCE_INJECTED_TOOLS.get(req.tool_name)
    if ds_type and not rewritten_params.get("datasourceUid"):
        try:
            uid = await mcp_client.resolve_datasource_uid(ds_type)
        except MCPUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Grafana MCP unavailable: {exc}",
            )
        if uid:
            rewritten_params = {**rewritten_params, "datasourceUid": uid}

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
    except MCPUnavailableError as exc:
        if cached_payload is not None:
            logger.warning(
                "Grafana MCP unavailable, serving stale cache",
                extra={"tool": req.tool_name, "error": str(exc)},
            )
            res_data = cached_payload
            is_stale = True
        else:
            # Nothing is synthesised to cover the gap: the caller surfaces a
            # degraded run rather than evidence the agent would treat as real.
            logger.error(
                "Grafana MCP unavailable and no cached data",
                extra={"tool": req.tool_name, "error": str(exc)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Grafana MCP unavailable: {exc}",
            )
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


@app.post("/write", response_model=MCPCallResponse)
async def write_mcp_tool(req: MCPWriteRequest) -> MCPCallResponse:
    """Executes an approved write back into Grafana Cloud.

    Kept separate from /call rather than folded into it behind a flag. The query
    path validates against an allowlist holding no write tool at all, so the agent
    cannot reach this behaviour however its prompt is manipulated; arriving here
    requires a caller that already holds an approval id.

    Deliberately uncached, unquantized and not deduplicated: those transformations
    are safe for reads and wrong for writes, which must reach Grafana exactly as
    the executor issued them. Rate limiting still applies, since writes share the
    upstream quota with every query.
    """
    global total_calls_count
    total_calls_count += 1
    start_time = time.time()

    try:
        validate_write_tool_allowed(req.tool_name)
    except ToolAllowlistError as exc:
        logger.warning(
            "Write allowlist rejection",
            extra={"tool": exc.tool_name, "tenant": req.tenant_id},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    acquired = await rate_limiter.acquire(timeout_sec=5.0)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Upstream Grafana MCP rate limit ceiling reached; write not attempted.",
        )

    try:
        result = await mcp_client.call_upstream_mcp(
            req.tool_name, req.parameters, req.tenant_id
        )
    except MCPUnavailableError as exc:
        # No stale-cache fallback here: pretending a write landed when it did not
        # would put a false record in front of the humans reading the dashboard.
        logger.error(
            "Grafana MCP unavailable for write",
            extra={"tool": req.tool_name, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Grafana MCP unavailable: {exc}",
        )
    except Exception as exc:
        logger.error(
            "Grafana MCP write failed",
            extra={"tool": req.tool_name, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Grafana MCP write failed: {exc}",
        )

    latency_ms = round((time.time() - start_time) * 1000.0, 2)
    call_logs.append(
        ToolCallLog(
            tool_name=req.tool_name,
            parameters={**req.parameters, "approval_id": req.approval_id},
            latency_ms=latency_ms,
            cache_hit=False,
            is_stale=False,
            tenant_id=req.tenant_id,
            run_id=req.run_id,
        )
    )
    logger.info(
        "Approved write applied to Grafana",
        extra={
            "tool": req.tool_name,
            "tenant": req.tenant_id,
            "run_id": req.run_id,
            "approval_id": req.approval_id,
        },
    )

    return MCPCallResponse(
        tool_name=req.tool_name,
        result=result,
        latency_ms=latency_ms,
        cache_hit=False,
        is_stale=False,
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
