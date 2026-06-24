#!/usr/bin/env python3
"""PostToolUse best-effort linter for TS/TSX — keeps the existing frontend tidy as the
backend is reworked under it. Non-blocking by contract: ALWAYS exits 0 so a lint hiccup
never interrupts the session, and it only touches files THIS main thread edits (it does
not reach across to Cursor's file-disjoint tasks).

Runs `eslint --fix` on a single edited .ts/.tsx file if eslint is resolvable. Silent no-op
otherwise (e.g. before `pnpm install`, or on non-TS files).
"""
import json
import os
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    path = (payload.get("tool_input", {}) or {}).get("file_path", "")
    if not path or not path.lower().endswith((".ts", ".tsx")):
        return 0
    if not os.path.exists(path):
        return 0

    try:
        # npx resolves the project-local eslint configured in package.json.
        subprocess.run(
            ["npx", "--no-install", "eslint", "--fix", path],
            timeout=60,
            capture_output=True,
            shell=os.name == "nt",  # npx is a .cmd shim on Windows
        )
    except Exception:
        pass  # best-effort only
    return 0


if __name__ == "__main__":
    sys.exit(main())
