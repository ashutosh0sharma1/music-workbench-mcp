"""Read-only real stdio MCP checks for the saved-file companion."""
import asyncio
import json
from pathlib import Path
import tempfile
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main(native=False):
    params = StdioServerParameters(command=str(ROOT / "runtime/workbench-venv/Scripts/python.exe"),
                                   args=[str(ROOT / "workbench_server.py")])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            required = {"read_composition", "write_composition", "plugin_catalog", "plugin_preset_catalog",
                        "plugin_parameter_catalog", "checkpoint_library", "restore_library", "list_autosaves",
                        "checkpoint_autosave", "workbench_status", "arrange_audio_project", "render_saved_project"}
            if not required <= set(names):
                raise RuntimeError("Required production tools missing")
            result = await session.call_tool("measure_audio", {"audio_path": str(ROOT / "runtime/synthetic-test.wav")})
            if result.isError:
                raise RuntimeError("Measurement tool failed")
            checks = {}
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                arranged = await session.call_tool("arrange_audio_project", {
                    "output_path": str(folder / "fixture.flp"),
                    "audio_paths": [str(ROOT / "runtime/synthetic-test.wav")], "tempo": 120})
                if arranged.isError:
                    raise RuntimeError("Offline fixture generation failed; no native FL launch")
                calls = [
                    ("project_preflight", {"project_path": str(folder / "fixture.flp")}),
                    ("validate_audio_export", {"audio_path": str(ROOT / "runtime/synthetic-test.wav"), "minimum_duration_seconds": 8}),
                    ("compare_mix_exports", {"before_path": str(ROOT / "runtime/synthetic-test.wav"),
                                             "after_path": str(ROOT / "runtime/synthetic-test.wav")}),
                    ("prepare_listening_excerpt", {"audio_path": str(ROOT / "runtime/synthetic-test.wav"),
                        "output_path": str(folder / "excerpt.wav"), "duration_seconds": 1}),
                    ("export_note_midi", {"output_path": str(folder / "notes.mid"),
                        "notes": [{"pitch": 60, "start": 0, "duration": 1}]}),
                ]
                for tool, arguments in calls:
                    called = await session.call_tool(tool, arguments)
                    if called.isError:
                        raise RuntimeError(f"{tool} stdio check failed")
                    checks[tool] = [c.text for c in called.content if c.type == "text"]
                    decoded = json.loads(checks[tool][0])
                    if tool == "validate_audio_export" and not decoded["accepted"]:
                        raise RuntimeError("Known non-silent fixture was rejected")
                    if tool == "project_preflight" and decoded["playlist_payload_bytes"] != [80]:
                        raise RuntimeError("Playlist wire payload was not preserved")
                async def checked(name, arguments):
                    response = await session.call_tool(name, arguments)
                    if response.isError:
                        raise RuntimeError(f"New MCP tool failed: {name}")
                    value = json.loads(next(c.text for c in response.content if c.type == "text"))
                    checks[name] = value
                    return value
                await checked("workbench_status", {})
                await checked("plugin_catalog", {"query": "3x Osc", "limit": 2})
                await checked("plugin_preset_catalog", {"plugin": "3x Osc", "limit": 2})
                await checked("plugin_parameter_catalog", {"plugin": "Fruity Parametric EQ 2", "limit": 2})
                full = await checked("read_composition", {"project_path": str(folder / "fixture.flp")})
                rebuilt = await checked("write_composition", {"output_path": str(folder / "rebuilt.flp"),
                    "composition": full["composition"]})
                if full["sha256"] != rebuilt["sha256"]:
                    raise RuntimeError("Full composition MCP roundtrip changed the original bytes")
                library = folder / "library"
                library.mkdir()
                (library / "synthetic.bin").write_bytes(b"test-only library fixture")
                captured = await checked("checkpoint_library", {"library_path": str(library)})
                await checked("restore_library", {"manifest_path": captured["manifest"],
                    "output_directory": str(folder / "library-restored")})
                await checked("list_autosaves", {"limit": 1})
                song = await checked("create_song", {"output_path": str(folder / "song.flp"), "tempo": 120,
                    "tracks": [{"name": "Factory test", "plugin": "3x Osc", "notes": [
                        {"pitch": 60, "start": 0, "duration": 16}],
                        "effects": [{"plugin": "Fruity Reeverb 2"}]}]})
                if native:
                    job = await checked("render_saved_project", {"project_path": song["project"],
                        "minimum_duration_seconds": 8, "timeout_seconds": 90})
                    for _ in range(220):
                        status = await checked("render_status", {"job_id": job["job_id"]})
                        if status["state"] not in ("queued", "running"):
                            break
                        await asyncio.sleep(.5)
                    if status["state"] != "completed":
                        raise RuntimeError("Real MCP-controlled FL render failed")
                backup = await session.call_tool("checkpoint_project", {"project_path": str(folder / "fixture.flp")})
                if backup.isError:
                    raise RuntimeError("Checkpoint stdio check failed")
                manifest = json.loads(next(c.text for c in backup.content if c.type == "text"))["manifest"]
                restored = await session.call_tool("restore_portable_checkpoint", {
                    "manifest_path": manifest, "output_path": str(folder / "restored.flp")})
                if restored.isError:
                    raise RuntimeError("Portable restore stdio check failed")
                checks["restore_portable_checkpoint"] = [c.text for c in restored.content if c.type == "text"]
                profile = await session.call_tool("save_parameter_map", {
                    "plugin_name": "Synthetic transport fixture (not a real plugin)", "plugin_version": "test-only",
                    "parameter_index": 0, "parameter_name": "Test", "observations": [{"normalized": .5, "display": "test"}]})
                if profile.isError:
                    raise RuntimeError("Parameter map stdio check failed")
                pid = json.loads(next(c.text for c in profile.content if c.type == "text"))["profile_id"]
                lookup = await session.call_tool("lookup_parameter_setting", {
                    "profile_id": pid, "plugin_name": "Synthetic transport fixture (not a real plugin)",
                    "plugin_version": "test-only", "display_value": "test"})
                if lookup.isError:
                    raise RuntimeError("Parameter lookup stdio check failed")
                checks["lookup_parameter_setting"] = [c.text for c in lookup.content if c.type == "text"]
            receipt = {"tools": names, "measurement": [c.text for c in result.content if c.type == "text"],
                       "offline_checks": checks, "native_fl_accessed": native, "cloud_called": False}
            (ROOT / "runtime/workbench-mcp-verification.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main("--native" in sys.argv), timeout=180))
