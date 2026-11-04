"""Native MCP-only live acceptance on our factory effect.flp fixture.

Never run on a user's song. Tests restore their own values, leave playback stopped,
and write a durable receipt even if an assertion fails. No keyboard/UI automation.
"""
import asyncio
import json
import time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main():
    receipt = {"computer_use": False, "started": time.time(), "checks": {}}
    params = StdioServerParameters(command=str(ROOT / "runtime/fl-venv/Scripts/python.exe"),
                                   args=[str(ROOT / "fl_server.py")])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(name, **arguments):
                result = await session.call_tool(name, arguments)
                payload = json.loads(next(c.text for c in result.content if c.type == "text"))
                if result.isError or (isinstance(payload, dict) and payload.get("ok") is False):
                    raise RuntimeError(f"{name}: {payload}")
                return payload

            await call("fl_ping")
            await asyncio.sleep(1)
            receipt["bridge"] = await call("fl_ping")
            state = await call("fl_get_project_state")
            # The runner checks the process command line separately. Also require
            # exact fixture features before any reversible native write.
            assert "26.1.7" in state["fl_version"] and state["channel_count"] == 1, state
            assert state["tempo_bpm"] == 120 and not state["playing"] and not state["recording"], state
            channels = await call("fl_get_channel_state")
            receipt["channels"] = channels
            plugins = await call("fl_plugin_list", track=1)
            assert "Fruity Reeverb 2" in json.dumps(plugins), plugins
            receipt["checks"]["global_channel_read_with_audio_filter"] = True

            async def edit_check(tool, arguments, scope, field, expected):
                before = await call("fl_take_snapshot", scope=scope)
                changed = False
                try:
                    result = await call(tool, **arguments)
                    changed = True
                    after = await call("fl_take_snapshot", scope=scope)
                    assert abs(after[field] - expected) < .002, (tool, result, after)
                finally:
                    if changed:
                        await call("fl_rollback_last_change")
                        restored = await call("fl_take_snapshot", scope=scope)
                        assert abs(restored[field] - before[field]) < .002, (tool, before, restored)
                receipt["checks"][tool + "_readback_rollback"] = True

            tempo_changed = False
            try:
                tempo_changed = True
                await call("fl_set_tempo", bpm=121)
                assert (await call("fl_get_tempo"))["bpm"] == 121
                receipt["checks"]["tempo_write_readback_restore"] = True
            finally:
                if tempo_changed:
                    await call("fl_set_tempo", bpm=120)
                    assert (await call("fl_get_tempo"))["bpm"] == 120

            for tool, args, scope, field, expected in [
                ("fl_set_mixer_volume", {"track":1,"value":.3}, "mixer_track:1", "vol_norm", .3),
                ("fl_set_mixer_pan", {"track":1,"value":-.25}, "mixer_track:1", "pan", -.25),
                ("fl_set_channel_volume", {"channel":0,"value":.4}, "channel:0", "vol_norm", .4),
                ("fl_set_channel_pan", {"channel":0,"value":.25}, "channel:0", "pan", .25),
                ("fl_set_channel_mute", {"channel":0,"state":True}, "channel:0", "mute", True),
                ("fl_set_mixer_mute", {"track":1,"state":True}, "mixer_track:1", "mute", True),
            ]:
                await edit_check(tool, args, scope, field, expected)

            parameters = await call("fl_plugin_get_params", track=1, slot=0)
            wet = next(p for p in parameters["params"] if "wet" in p["name"].lower())
            receipt["plugin_observation"] = wet
            target = .25 if abs(wet["v"] - .25) > .1 else .75
            await edit_check("fl_plugin_set_param", {"track":1,"slot":0,"param":wet["i"],"value":target},
                             f"plugin_param:1:0:{wet['i']}", "v", target)

            before = await call("fl_take_snapshot", scope="mixer_track:1")
            await call("fl_set_dry_run", enabled=True)
            try:
                planned = await call("fl_set_mixer_volume", track=1, value=.1)
                assert planned["dry_run"] is True
                assert (await call("fl_take_snapshot", scope="mixer_track:1")) == before
                receipt["checks"]["dry_run_prevents_write"] = True
            finally:
                await call("fl_set_dry_run", enabled=False)

            try:
                await call("fl_play")
                await asyncio.sleep(.25)
                assert (await call("fl_get_play_state"))["playing"] is True
            finally:
                await call("fl_stop")
                assert (await call("fl_get_play_state"))["playing"] is False
            receipt["checks"]["play_stop_readback"] = True
            receipt["final_state"] = await call("fl_get_project_state")
            receipt["passed"] = True
    return receipt


if __name__ == "__main__":
    # Do not write to arbitrary interactive sessions, even if they happen to have
    # one channel at120 BPM. Process metadata is read-only, not UI automation.
    import subprocess
    processes = json.loads(subprocess.check_output([
        "powershell.exe", "-NoProfile", "-Command",
        "@(Get-CimInstance Win32_Process -Filter \"Name='FL64.exe'\" | Select-Object CommandLine) | ConvertTo-Json -Compress"
    ], text=True))
    if isinstance(processes, dict):
        processes = [processes]
    expected = str(ROOT / "runtime/native-acceptance-20261022-1792672847392814000/effect.flp")
    assert len(processes) == 1 and expected.lower() in processes[0]["CommandLine"].lower(), "Not our fixture session"
    receipt_path = ROOT / "runtime/fl26-live-write-verification.json"
    try:
        final = asyncio.run(asyncio.wait_for(main(), timeout=120))
    except Exception as error:
        receipt_path.write_text(json.dumps({"passed": False, "error": str(error)}, indent=2), encoding="utf-8")
        raise
    receipt_path.write_text(json.dumps(final, indent=2), encoding="utf-8")
    print(json.dumps(final, indent=2))
