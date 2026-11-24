"""Project-local entry points for the upstream SDK, CLI and servers."""

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def configure():
    os.chdir(ROOT)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    os.environ.setdefault("TOOLUNIVERSE_HOME", str(ROOT / ".tooluniverse"))
    os.environ.setdefault("TOOLUNIVERSE_CACHE_DIR", str(ROOT / ".tooluniverse" / "cache"))


def main():
    configure()
    mode = sys.argv.pop(1) if len(sys.argv) > 1 else "cli"
    if mode == "cli":
        from tooluniverse_cli_entry import main as entry
    elif mode == "api":
        from tooluniverse.http_api_server_cli import run_http_api_server as entry
    elif mode == "mcp":
        from tooluniverse.smcp_server import run_default_stdio_server as entry
    elif mode == "doctor":
        from tooluniverse.doctor import main as entry
    elif mode == "workbench":
        from workbench.app import main as entry
    else:
        raise SystemExit(f"Unknown mode: {mode}. Choose cli, api, mcp, doctor or workbench.")
    entry()


if __name__ == "__main__":
    main()
