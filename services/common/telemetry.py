"""OpenTelemetry and structured logging configuration for Studio Production Commander services."""
from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional
from opentelemetry import trace
from opentelemetry.trace import Tracer, Span


def setup_logging(service_name: str, log_level: str = "INFO") -> logging.Logger:
    """Configures structured JSON logging for the service."""
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Avoid duplicate handlers if called multiple times
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            '{"timestamp": "%(asctime)s", "service": "' + service_name + '", '
            '"level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}'
        ))
        logger.addHandler(handler)
    
    return logger


def get_tracer(service_name: str) -> Tracer:
    """Returns an OpenTelemetry tracer for the service."""
    return trace.get_tracer(service_name)


def start_span(
    tracer: Tracer,
    name: str,
    tenant_id: Optional[str] = None,
    run_id: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None
) -> Span:
    """Creates a span pre-populated with standard multi-tenant and run attributes."""
    span_attrs = attributes.copy() if attributes else {}
    if tenant_id:
        span_attrs["tenant_id"] = tenant_id
    if run_id:
        span_attrs["run_id"] = run_id
    
    return tracer.start_span(name, attributes=span_attrs)
