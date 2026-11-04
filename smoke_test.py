"""Real stdio integration checks; Google is called only with --cloud."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import uuid
import wave

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"


def fixture():
    # Each invocation owns its fixture. The old shared path could be truncated
    # while another MCP acceptance client was decoding it.
    path = RUNTIME / ("smoke-fixture-" + uuid.uuid4().hex + ".wav")
    rate = 22050
    with wave.open(str(path), "wb") as out:
        out.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        frames = bytearray()
        for i in range(rate * 8):
            t = i / rate
            beat = t % .5
            tone = .15 * math.sin(2 * math.pi * 220 * t)
            pulse = .35 * math.exp(-beat * 40) * math.sin(2 * math.pi * 80 * t)
            sample = int(32767 * (tone + pulse))
            frames.extend(struct.pack("<hh", sample, sample))
        out.writeframes(frames)
    canonical = RUNTIME / "synthetic-test.wav"
    if not canonical.exists():
        try:
            with canonical.open("xb") as output:
                output.write(path.read_bytes())
        except FileExistsError:
            pass
    return path


async def run_server(name, command, args, calls):
    started = time.perf_counter()
    async with stdio_client(StdioServerParameters(command=str(command), args=list(map(str, args)), env={k: os.environ[k] for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") if k in os.environ})) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            results = {"server": name, "tools": [t.name for t in tools], "calls": []}
            for tool, arguments in calls:
                result = await session.call_tool(tool, arguments)
                if name != "fl-studio" and result.isError:
                    raise RuntimeError(f"{name}/{tool} failed")
                results["calls"].append({"tool": tool, "is_error": bool(result.isError),
                    "content": [c.text for c in result.content if c.type == "text"]})
            results["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            return results


async def main(cloud, fl):
    audio = fixture()
    results = [await run_server("audio-analyzer", RUNTIME / "audio-analyzer/mcp-server.exe", [], [
        ("audio_info", {"path": str(audio)}),
        ("spectral_features", {"path": str(audio)}),
        ("harmonic_analysis", {"path": str(audio)}),
        ("rhythm_analysis", {"path": str(audio)}),
        ("full_analysis", {"path": str(audio)}),
        ("compare", {"path_a": str(audio), "path_b": str(audio)}),
    ])]
    results.append(await run_server("gemini-music", sys.executable, [ROOT / "gemini_server.py"],
        [("analyze_audio", {"audio_path": str(audio), "prompt": (
            "Describe only what you hear: sustained versus percussive sounds, vocals if any, "
            "and approximate rhythm. Do not assume real instruments. Keep under 150 words."
        )})] if cloud else []))
    if fl:
        results.append(await run_server("fl-studio", RUNTIME / "fl-venv/Scripts/python.exe",
            [ROOT / "fl_server.py"], [("fl_ping", {})]))
    receipt = "smoke-cloud-results.json" if cloud else "smoke-local-results.json"
    (RUNTIME / receipt).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--fl", action="store_true")
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(main(args.cloud, args.fl), timeout=180))

