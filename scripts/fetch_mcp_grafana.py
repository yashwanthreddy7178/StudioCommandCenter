#!/usr/bin/env python3
"""Downloads the upstream mcp-grafana binary into .tools/.

Kept out of the repository: it is a 54 MB third-party artifact with its own
release cadence, so it is fetched from Grafana's GitHub releases on demand and
.tools/ is gitignored.
"""
from __future__ import annotations

import io
import json
import platform
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = REPO_ROOT / ".tools" / "mcp-grafana"
RELEASES_API = "https://api.github.com/repos/grafana/mcp-grafana/releases/latest"

ASSET_FOR_PLATFORM = {
    ("Windows", "AMD64"): "mcp-grafana_Windows_x86_64.zip",
    ("Windows", "ARM64"): "mcp-grafana_Windows_arm64.zip",
    ("Linux", "x86_64"): "mcp-grafana_Linux_x86_64.tar.gz",
    ("Linux", "aarch64"): "mcp-grafana_Linux_arm64.tar.gz",
    ("Darwin", "x86_64"): "mcp-grafana_Darwin_x86_64.tar.gz",
    ("Darwin", "arm64"): "mcp-grafana_Darwin_arm64.tar.gz",
}


def main() -> int:
    key = (platform.system(), platform.machine())
    asset_name = ASSET_FOR_PLATFORM.get(key)
    if not asset_name:
        print(f"No published mcp-grafana build for {key[0]}/{key[1]}.")
        print("Build from source: https://github.com/grafana/mcp-grafana")
        return 1

    print(f"Looking up the latest release for {asset_name} ...")
    with urllib.request.urlopen(RELEASES_API, timeout=30) as response:
        release = json.load(response)

    url = next(
        (a["browser_download_url"] for a in release["assets"] if a["name"] == asset_name),
        None,
    )
    if not url:
        print(f"Release {release.get('tag_name')} has no asset named {asset_name}.")
        return 1

    print(f"Downloading {release.get('tag_name')} ...")
    with urllib.request.urlopen(url, timeout=300) as response:
        payload = response.read()

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    if asset_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(TARGET_DIR)
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            archive.extractall(TARGET_DIR)

    binary = TARGET_DIR / ("mcp-grafana.exe" if key[0] == "Windows" else "mcp-grafana")
    if not binary.exists():
        print(f"Extraction finished but {binary.name} is not present in {TARGET_DIR}.")
        return 1
    if key[0] != "Windows":
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    print(f"Installed {binary} ({binary.stat().st_size // 1024 // 1024} MB)")
    print("Start it with:  python scripts/run_mcp_grafana.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
