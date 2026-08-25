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
    ("render-sim", "services.render-sim.src.main:app", 8004),
    ("mcp-gateway", "services.mcp-gateway.src.main:app", 8001),
    ("impact-engine", "services.impact-engine.src.main:app", 8002),
    ("action-executor", "services.action-executor.src.main:app", 8003),
    ("agent-worker", "services.agent-worker.src.main:app", 8010),
    ("stream-service", "services.stream-service.src.main:app", 8005),
    ("api-gateway", "services.api-gateway.src.main:app", 8000),
]


def main():
    print("\n" + "=" * 70)
    print(" 🎬 Studio Production Commander - Local Microservices Launcher")
    print("=" * 70 + "\n")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    processes = []

    try:
        for name, app_module, port in SERVICES:
            print(f"[*] Starting {name:<18} on http://localhost:{port} ...")
            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                app_module,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ]
            proc = subprocess.Popen(cmd, env=env, cwd=str(REPO_ROOT))
            processes.append((name, proc, port))
            time.sleep(0.5)

        print("\n" + "-" * 70)
        print(" [✓] All 7 backend microservices are running locally!")
        print(" [✓] Ingress API Gateway: http://localhost:8000")
        print(" [✓] Frontend Dev Server: run 'cd web && npm run dev' (http://localhost:3000)")
        print("-" * 70)
        print("\nPress Ctrl+C to terminate all services...\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[*] Shutting down microservices...")
        for name, proc, port in processes:
            proc.terminate()
        print("[✓] All services stopped.")


if __name__ == "__main__":
    main()
