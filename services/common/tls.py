"""System trust store integration for outbound TLS.

Any service making outbound HTTPS calls needs this on a network that inspects
TLS: the served certificate is signed by a local root present in the OS store but
absent from the certifi bundle Python uses by default, so verification fails and
every call errors. It is a no-op inside a container, where certifi is correct and
truststore is not installed.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_applied = False


def enable_system_trust_store() -> bool:
    """Routes TLS verification through the OS certificate store. Idempotent."""
    global _applied
    if _applied:
        return True
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    _applied = True
    logger.info("TLS verification delegated to the system trust store")
    return True
