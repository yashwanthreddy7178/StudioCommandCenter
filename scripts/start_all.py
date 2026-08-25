#!/usr/bin/env python3
"""Local development orchestrator for Studio Production Commander.

Starts all backend microservices and displays structured status.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SERVICES = [
    ("render-sim", 8004),
    ("mcp-gateway", 8001),
    ("impact-engine", 8002),
    ("action-executor", 8003),
    ("agent-worker", 8010),
    ("stream-service", 8005),
    ("api-gateway", 8000),
]


def main() -> None:
    print()
    print("=" * 70)
    print(" Studio Production Commander - Local Microservices Launcher")
    print("=" * 70)
    print()

    processes = []

    try:
        for name, port in SERVICES:
            service_dir = REPO_ROOT / "services" / name
            env = os.environ.copy()
            # `src.*` resolves from the service directory, `services.common.*` from the repo root.
            env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(service_dir)])

            print(f"[*] Starting {name:<18} on http://localhost:{port}")
            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "src.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ]
            proc = subprocess.Popen(cmd, env=env, cwd=str(service_dir))
            processes.append((name, proc, port))
            time.sleep(0.5)

        print()
        print("-" * 70)
        print(" All 7 backend microservices are running.")
        print(" API Gateway:  http://localhost:8000")
        print(" Frontend:     cd web && npm run dev")
        print("-" * 70)
        print()
        print("Press Ctrl+C to terminate all services.")
        print()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print()
        print("[*] Shutting down microservices...")
        for name, proc, _port in processes:
            proc.terminate()
        print("[*] All services stopped.")


if __name__ == "__main__":
    main()
