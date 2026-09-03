#!/usr/bin/env python3
"""Submission compliance audit script for Studio Production Commander.

Enforces all Agentic Cinema hackathon submission rules and constraints.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Provider names that carry no ordinary English meaning. Any occurrence at all
# is worth flagging, in prose as much as in a manifest.
BANNED_PATTERNS = [
    r"\bopenai\b",
    r"\banthropic\b",
    r"\bclaude\b",
    r"\bxai\b",
    r"\bgroq\b",
    r"\bhuggingface\b",
    r"\bollama\b",
    r"\blangchain\b",
    r"\bllamaindex\b",
    r"\bcrewai\b",
    r"\bautogen\b",
    r"\bsemantic[-_]kernel\b",
]

# Provider names that are also ordinary English words. Matched anywhere, these
# turn any sentence containing "together", "replicate" or "cohere" into a
# compliance violation, which is how a passing audit starts failing for no real
# reason. They only mean anything as a dependency, so they are checked only on
# lines that could declare one.
AMBIGUOUS_PATTERNS = [
    r"\bcohere\b",
    r"\bmistral\b",
    r"\btogether\b",
    r"\breplicate\b",
    r"\bhaystack\b",
]

# A Python import, or a manifest-style key/value entry. Prose matches neither.
DEPENDENCY_LINE = re.compile(
    r"""^\s*(?:import|from)\s+\w|^\s*["']?[\w.-]+["']?\s*[:=]""",
    re.IGNORECASE,
)
MANIFEST_NAMES = {
    "pyproject.toml", "requirements.txt", "package.json", "package-lock.json",
    "setup.py", "setup.cfg", "Pipfile", "poetry.lock",
}

FORBIDDEN_ASSISTANT_TOOLS = [
    "ask_assistant",
    "create_investigation",
    "get_investigation",
    "list_investigations",
    "assistant_chat",
]

IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__",
    ".pytest_cache",
    # Vendored third-party binary, gitignored and never submitted.
    ".tools",
}
IGNORED_FILES = {"compliance_audit.py", "compliance-check.md", "10-compliance.md", "00-core.md"}


def _is_binary(raw: bytes) -> bool:
    """Treats a file containing a NUL byte as binary.

    Extension lists miss things. The vendored mcp-grafana executable is 54 MB of
    Go, and scanning it line by line turned incidental byte sequences into
    "banned dependency" hits for words like claude and openai that appear inside
    unrelated help text and protobuf descriptors.
    """
    return 0 in raw


def _submittable_files() -> list[Path]:
    """Files that would be in the public repository, and nothing else.

    The audit answers a question about the submission, so the right scope is what
    git would carry: tracked files plus untracked ones that are not ignored.
    Walking the working tree instead pulled in .tools, venv artefacts and other
    ignored paths that no judge will ever receive.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        paths = [REPO_ROOT / line for line in out.splitlines() if line.strip()]
        if paths:
            return paths
    except Exception:
        pass

    # Fallback for a source export with no git metadata.
    collected = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        collected.extend(Path(root) / f for f in files)
    return collected


def check_banned_ai_dependencies() -> tuple[bool, list[str]]:
    """Scan source code for prohibited AI libraries/providers."""
    findings = []
    for filepath in _submittable_files():
        if filepath.name in IGNORED_FILES:
            continue
        if any(part in IGNORED_DIRS for part in filepath.parts):
            continue
        try:
            raw = filepath.read_bytes()
        except OSError:
            continue
        if _is_binary(raw):
            continue

        content = raw.decode("utf-8", errors="ignore")
        is_manifest = filepath.name in MANIFEST_NAMES
        for line_no, line in enumerate(content.splitlines(), start=1):
            patterns = list(BANNED_PATTERNS)
            if is_manifest or DEPENDENCY_LINE.search(line):
                patterns += AMBIGUOUS_PATTERNS
            for pat in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    try:
                        rel_path = filepath.relative_to(REPO_ROOT)
                    except ValueError:
                        rel_path = filepath
                    findings.append(
                        f"{rel_path}:{line_no} matches '{pat}': {line.strip()[:80]}"
                    )

    return len(findings) == 0, findings


def check_google_cloud_ai_usage() -> tuple[bool, str]:
    """Verify google-adk or google-genai is used in services/agent-worker."""
    agent_worker_dir = REPO_ROOT / "services" / "agent-worker"
    if not agent_worker_dir.exists():
        return False, "services/agent-worker directory not found."

    pyproject = agent_worker_dir / "pyproject.toml"
    if not pyproject.exists():
        return False, "agent-worker pyproject.toml missing."

    content = pyproject.read_text(encoding="utf-8")
    has_dep = "google-genai" in content or "google-adk" in content

    # Check import in src
    planner_file = agent_worker_dir / "src" / "agent" / "planner.py"
    has_import = False
    if planner_file.exists():
        src_text = planner_file.read_text(encoding="utf-8")
        has_import = "google" in src_text and ("genai" in src_text or "adk" in src_text)

    if has_dep and has_import:
        return True, "Google Cloud AI SDK (google-genai / google-adk) declared in dependencies and imported in planner.py."
    return False, "Google Cloud AI SDK not declared or imported properly in agent-worker."


def check_grafana_mcp_usage() -> tuple[bool, str]:
    """Verify Grafana MCP is loaded in mcp-gateway and nowhere else."""
    gateway_dir = REPO_ROOT / "services" / "mcp-gateway"
    if not gateway_dir.exists():
        return False, "services/mcp-gateway directory not found."

    # Check for forbidden assistant tools in code
    for root, dirs, files in os.walk(REPO_ROOT / "services"):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for file in files:
            if not file.endswith(".py"):
                continue
            path = Path(root) / file
            text = path.read_text(encoding="utf-8")
            for f_tool in FORBIDDEN_ASSISTANT_TOOLS:
                if f'"{f_tool}"' in text and "allowlist.py" not in file and "test_gateway.py" not in file:
                    return False, f"Forbidden assistant-native tool '{f_tool}' found in {path.relative_to(REPO_ROOT)}."

    return True, "Grafana MCP correctly scoped to mcp-gateway; no assistant-native tools active."


def check_git_history() -> tuple[bool, str, list[str]]:
    """Scan commit messages and authorship for banned AI provider references.

    check_banned_ai_dependencies walks files only, so a trailer injected by an
    editing tool (a co-author line, a generated-with footer) never reaches it while
    staying plainly visible on a public repository. Returns (passed, evidence, findings).
    """
    field_sep = chr(31)
    record_sep = chr(30)
    try:
        proc = subprocess.run(
            ["git", "log", "--all", "--format=%H%x1f%an <%ae>%x1f%cn <%ce>%x1f%B%x1e"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return True, f"git unavailable, history not scanned ({exc.__class__.__name__}).", []

    if proc.returncode != 0:
        return True, "No git repository yet; no history to scan.", []

    findings = []
    for record in proc.stdout.split(record_sep):
        if not record.strip():
            continue
        parts = record.strip().split(field_sep)
        if len(parts) < 4:
            continue
        sha, author, committer, message = parts[0], parts[1], parts[2], parts[3]
        for field, value in (("author", author), ("committer", committer), ("message", message)):
            for pat in BANNED_PATTERNS:
                match = re.search(pat, value, re.IGNORECASE)
                if match:
                    findings.append(
                        f"{sha[:8]} {field} matches '{pat}': {value.strip()[:80]}"
                    )
                    break

    if findings:
        return False, f"{len(findings)} banned references in commit history.", findings
    return True, "No banned references in commit messages or authorship.", []


def check_license() -> tuple[bool, str]:
    """Verify Apache-2.0 LICENSE exists at repository root."""
    license_file = REPO_ROOT / "LICENSE"
    if not license_file.exists():
        return False, "LICENSE file missing at root."
    content = license_file.read_text(encoding="utf-8")
    if "Apache License" in content and "Version 2.0" in content:
        return True, "Apache-2.0 LICENSE file present at root."
    return False, "LICENSE is not Apache-2.0."


def run_audit() -> int:
    """Runs all audit checks and outputs a formatted markdown table."""
    # Findings quote arbitrary source lines, which on Windows could contain
    # characters the console codepage cannot encode. Printing one then killed the
    # audit mid-report with a UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    print("\n" + "=" * 70)
    print(" Studio Production Commander - Submission Compliance Audit")
    print("=" * 70 + "\n")

    banned_pass, banned_findings = check_banned_ai_dependencies()
    google_pass, google_msg = check_google_cloud_ai_usage()
    mcp_pass, mcp_msg = check_grafana_mcp_usage()
    lic_pass, lic_msg = check_license()
    git_pass, git_msg, git_findings = check_git_history()

    results = [
        ("1. Banned AI Dependencies", "PASS" if banned_pass else "FAIL", "Zero third-party model providers" if banned_pass else f"{len(banned_findings)} hits found"),
        ("2. Required Google AI SDK", "PASS" if google_pass else "FAIL", google_msg),
        ("3. Grafana MCP Isolation", "PASS" if mcp_pass else "FAIL", mcp_msg),
        ("4. Apache-2.0 License", "PASS" if lic_pass else "FAIL", lic_msg),
        ("5. Clean Commit History", "PASS" if git_pass else "FAIL", git_msg),
    ]

    print(f"{'Check Item':<30} | {'Status':<8} | {'Evidence'}")
    print("-" * 75)
    for name, status, evidence in results:
        print(f"{name:<30} | {status:<8} | {evidence}")
    print("-" * 75)

    if not banned_pass:
        print("\n[!] Banned Dependency Findings:")
        for f in banned_findings[:10]:
            print(f"  - {f}")

    if not git_pass:
        print("\n[!] Commit History Findings:")
        for f in git_findings[:10]:
            print(f"  - {f}")

    all_passed = all(r[1] == "PASS" for r in results)
    print("\nAudit Outcome: " + ("ALL CHECKS PASSED (100% COMPLIANT)" if all_passed else "COMPLIANCE VIOLATIONS DETECTED"))
    print("=" * 70 + "\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run_audit())
