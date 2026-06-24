#!/usr/bin/env python3
"""PreToolUse guard for Paper Pigeon — blocks writes to secrets, env files, and lock files.

Wired in .claude/settings.json on Edit|Write|MultiEdit. Reads the Claude Code hook
payload from stdin; exit code 2 + a stderr message blocks the tool call and feeds the
reason back to the model. Exit 0 allows.

Rationale (see 04-infrastructure-security-and-roadmap.md → Security): real secrets live in
a gitignored .env locally and in a managed secret store when deployed. No agent — this main
thread, a subagent, Cursor, or Copilot — should ever write a secret into a tracked file.
Lock files are blocked so dependency changes go through `pnpm add` / `pip` deliberately,
not by hand-editing resolved trees.
"""
import json
import os
import sys

# Path fragments that must never be written by an automated edit.
BLOCKED_SUBSTRINGS = (
    ".env",            # any .env / .env.production / .env.* — except the example below
    ".pem",
    ".key",
    "id_rsa",
    "credentials",     # ~/.aws/credentials and friends
    "secrets.json",
)
# Exact basenames that are allowed even though they match a blocked substring.
ALLOWLIST_BASENAMES = {".env.example"}
# Lock files: change deps via the package manager, not by editing resolved trees.
BLOCKED_BASENAMES = {"pnpm-lock.yaml", "package-lock.json", "poetry.lock", "uv.lock"}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # If we can't parse the payload, fail open — don't wedge the session.
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path:
        return 0

    norm = path.replace("\\", "/").lower()
    base = os.path.basename(norm)

    if base in ALLOWLIST_BASENAMES:
        return 0

    if base in BLOCKED_BASENAMES:
        sys.stderr.write(
            f"Blocked: '{base}' is a lock file. Change dependencies with pnpm/pip, "
            "not by editing the resolved lock tree.\n"
        )
        return 2

    for frag in BLOCKED_SUBSTRINGS:
        if frag in norm:
            sys.stderr.write(
                f"Blocked: '{path}' looks like a secret/credential file ({frag!r}). "
                "Secrets belong in a gitignored .env (local) or the managed secret store "
                "(deployed) — never in a tracked, agent-edited file. Use .env.example for "
                "the documented key names.\n"
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
