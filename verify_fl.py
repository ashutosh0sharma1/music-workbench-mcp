"""Read-only live MIDI/FL MCP acceptance. No project edits or playback."""
import asyncio
import json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main():
    params = StdioServerParameters(
        command=str(ROOT / "runtime/fl-venv/Scripts/python.exe"),
        args=[str(ROOT / "fl_server.py")],
    )
    receipt = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            receipt["tool_count"] = len(names)
            forbidden = {"fl_write_piano_roll_notes", "fl_quantize_pattern", "fl_write_raga_chords",
                         "fl_write_raga_melody", "fl_analyze_audio", "fl_extract_melody"}
            assert not (names & forbidden), "Legacy GUI/model tools must not be exposed"
            receipt["legacy_gui_tools_disabled"] = True
            # Opening the bridge on first ping starts heartbeat reception.
            await session.call_tool("fl_ping", {})
            await asyncio.sleep(1)
            for name in ["fl_ping", "fl_get_tempo", "fl_get_project_state", "fl_list_installed_plugins"]:
                result = await session.call_tool(name, {})
                receipt[name] = {"is_error": bool(result.isError), "content": [
                    c.text for c in result.content if c.type == "text"
                ]}
                if result.isError:
                    raise RuntimeError(f"Live check failed: {name}")
                if name == "fl_ping" and not json.loads(receipt[name]["content"][0]).get("alive"):
                    raise RuntimeError("FL heartbeat is not alive")
    output = ROOT / "runtime/fl-live-verification.json"
    output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60))
