"""Check the real local Codex protocol without logging in or invoking inference."""
import asyncio
import json

from run import ROOT, configure
configure()
from workbench.codex_bridge import CodexBridge, RESEARCH_INSTRUCTIONS
from workbench.tools import ScientificTools, dynamic_tools


async def main():
    runtime = ROOT / ".tooluniverse" / "runtime" / "workbench" / "protocol-check"
    tools = ScientificTools()
    bridge = CodexBridge(runtime, tools)
    try:
        await bridge.start()
        account = await bridge.account()
        models = await bridge.models()
        response = await bridge.rpc("thread/start", {
            "cwd": str(runtime / "research"), "sandbox": "read-only", "approvalPolicy": "never",
            "ephemeral": True, "baseInstructions": RESEARCH_INSTRUCTIONS,
            "dynamicTools": dynamic_tools(), "serviceName": "tooluniverse_protocol_check",
        }, timeout=60)
        assert response.get("thread", {}).get("id"), "Thread creation failed"
        report = {"initialize": "passed", "account_read": "passed", "model_catalog_count": len(models),
                  "dynamic_tools_thread_start": "passed", "inference": "not_run_requires_user_login",
                  "isolated_profile_authenticated": account["connected"]}
        (runtime / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await bridge.close()
        await tools.client.aclose()


asyncio.run(main())
