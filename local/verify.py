"""Verify the installed SDK, an actual MCP session, and optionally HTTP/network."""
import argparse
import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
import subprocess
import sys

from run import ROOT, configure

configure()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sdk_check(online):
    from tooluniverse import ToolUniverse

    tu = ToolUniverse()
    try:
        tu.load_tools()
        names = {tool["name"] for tool in tu.all_tools}
        require("UniProt_get_entry_by_accession" in names, "UniProt tool was not loaded")
        summary = {"version": version("tooluniverse"), "loaded_tools": len(names)}
        if online:
            result = tu.run({
                "name": "UniProt_get_entry_by_accession",
                "arguments": {"accession": "P05067", "include_publications": False},
            }, use_cache=False)
            require(isinstance(result, dict), "UniProt did not return an object")
            require(result.get("status") == "success", f"UniProt failed: {result.get('error')}")
            require(result.get("data", {}).get("primaryAccession") == "P05067", "Unexpected UniProt accession")
            summary["uniprot_accession"] = "P05067"
        return summary
    finally:
        tu.close()


async def mcp_check():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "local" / "run.py"), "mcp"],
        cwd=str(ROOT),
        env=dict(os.environ),
    )
    log = ROOT / ".tooluniverse" / "runtime" / "mcp-verify.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                expected = {"list_tools", "grep_tools", "get_tool_info", "execute_tool"}
                require(expected <= names, f"MCP missing tools: {expected - names}")
                result = await session.call_tool("list_tools", {"mode": "names", "limit": 3})
                require(not result.isError, "MCP list_tools returned a protocol error")
                require(bool(result.content), "MCP list_tools returned no content")
                return {"exposed_tools": sorted(names), "list_tools_call": "passed"}


def http_check(base_url):
    import requests

    headers = {}
    if os.environ.get("TOOLUNIVERSE_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["TOOLUNIVERSE_API_TOKEN"]
    with requests.Session() as client:
        client.headers.update(headers)
        response = client.get(base_url + "/health", timeout=10)
        response.raise_for_status()
        require(response.json().get("status") == "healthy", "API is not healthy")
        response = client.get(base_url + "/docs", timeout=10)
        response.raise_for_status()
        require("swagger-ui" in response.text, "Swagger page is missing")
        response = client.get(base_url + "/api/methods", timeout=10)
        response.raise_for_status()
        methods = response.json()
        require(any(item["name"] == "run" for item in methods["methods"]), "API run method is missing")
        response = client.post(base_url + "/api/call", json={"method": "load_tools", "kwargs": {}}, timeout=90)
        response.raise_for_status()
        require(response.json().get("success"), "API load_tools failed")
        response = client.get(base_url + "/health", timeout=10)
        response.raise_for_status()
        health = response.json()
        require(health.get("loaded_tools_count", 0) > 0, "HTTP API did not load any tools")
        return {"url": base_url, "method_count": methods["total"], **health}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true", help="Also query the public UniProt API")
    parser.add_argument("--api-url", help="Also verify a running local HTTP API")
    args = parser.parse_args()
    checks = {}

    def check(name, function):
        try:
            checks[name] = {"passed": True, "details": function()}
        except Exception as exc:
            checks[name] = {"passed": False, "error": str(exc)}
        print(json.dumps({name: checks[name]}, ensure_ascii=False), flush=True)

    def dependencies():
        result = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True, encoding="utf-8", timeout=30)
        require(result.returncode == 0, result.stdout + result.stderr)
        return result.stdout.strip()

    check("dependencies", dependencies)
    check("sdk", lambda: sdk_check(args.online))
    check("mcp", lambda: asyncio.run(asyncio.wait_for(mcp_check(), timeout=90)))
    if args.api_url:
        check("http_api", lambda: http_check(args.api_url.rstrip("/")))
    report = {"time_utc": datetime.now(timezone.utc).isoformat(), "checks": checks}
    report_path = ROOT / ".tooluniverse" / "runtime" / "verification.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if all(item["passed"] for item in checks.values()) else 1)


if __name__ == "__main__":
    main()
