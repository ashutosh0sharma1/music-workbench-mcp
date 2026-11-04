"""Exercise the reviewed pinned candidate's file backend, without native FL access.

Run with runtime/workbench-venv/Scripts/python.exe. Source snapshot is kept under
ignored runtime/research-20261021; this does not install or register another MCP.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "runtime/research-20261021/kuruitsugizaki"
PIN = "d4b9567c65fe1e2e95ab13b0b5cdac78d769be05"


def block_live_access(name, args):
    if name in {"subprocess.Popen", "os.system", "socket.connect", "socket.bind", "ctypes.dlopen",
                "winreg.CreateKey", "winreg.SetValue", "winreg.DeleteKey", "winreg.DeleteValue"}:
        raise RuntimeError(f"Offline candidate audit prohibits {name}")


def main():
    metadata = json.loads((SOURCE / "source-index.json").read_text())
    if metadata["sha"] != PIN:
        raise ValueError("Expected reviewed source pin")
    sys.addaudithook(block_live_access)
    sys.path.insert(0, str(SOURCE / "scripts"))
    import config
    config.FILE = SOURCE / "offline-audit-config.json"
    config.FILE.write_text(json.dumps({
        "template": str(SOURCE / "projects/init/init.flp"),
        "effect_template": str(SOURCE / "projects/init/fx.flp"),
        "plugin_db": "C:/Image-Line/FL Studio/Presets/Plugin database",
        "library": None, "sample_folders": [], "projects_folder": [],
    }), encoding="utf-8")
    import flp
    import writer
    import project
    import view
    import library
    result = {"pin": PIN, "checks": {}, "native_fl_accessed": False,
              "cloud_called": False, "mcp_installed": False}
    for label, path in {
        "template": SOURCE / "projects/init/init.flp",
        "effect_template": SOURCE / "projects/init/fx.flp",
        "local_native_project": Path("C:/Image-Line/FL Studio/Projects/MCP-Setup-Test-20261020/MCP-Setup-Test-20261020.flp"),
        "our_corrected_fixture": ROOT / "runtime/workbench-fixture-preserved-20261021.flp",
    }.items():
        raw = path.read_bytes()
        identical = flp.serialize(flp.parse(raw)) == raw
        if not identical:
            raise AssertionError(f"Byte roundtrip failed: {label}")
        result["checks"][label + "_byte_roundtrip"] = True
    fixture = ROOT / "runtime/synthetic-test.wav"
    original_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    doc = {
        "format": "compact", "project": {"bpm": 120, "ppq": 96, "title": "Kuru offline audit"},
        "instruments": [{"id": "lead", "name": "Factory 3x Osc", "kind": "plugin", "plugin": "3x Osc", "mixer": 1}],
        "samples": [{"id": "test_audio", "name": "Synthetic test", "kind": "audio", "path": str(fixture), "mixer": 2}],
        "automation": [{"id": "fade", "name": "Master fade", "kind": "automation", "target": {"mixer": 0, "param": "vol"},
                        "points": [[0, .8], [14, .8], [16, 0]]}],
        "mixer": {"volume": {"1": .7, "2": .6}, "routes": [{"from": 1, "to": 2, "level": .2}],
                  "effects": [{"track": 1, "slot": 0, "plugin": "Fruity Limiter"}]},
        "patterns": [{"id": "chord", "notes": [[1, 1, 0, 384, key, 80, "lead"] for key in ("C4", "E4", "G4")]}],
        "arrangements": [{"name": "Audit", "clips": [
            [1, 1, 1, 0, 384, "chord"], [2, 1, 1, 0, 1536, "s0"], [3, 1, 1, 0, 1536, "a0"]]}],
    }
    raw, warnings = writer.build(doc)
    if warnings:
        raise AssertionError(f"Candidate writer warnings: {warnings}")
    out = SOURCE / "offline-acceptance.flp"
    out.write_bytes(raw)
    readback = project.export(str(out))
    compact = view.read(str(out), "compact")
    notes = sum(len(p["notes"]) for p in compact["patterns"])
    clips = sum(len(a["clips"]) for a in compact["arrangements"])
    effects = compact.get("mixer_effects", [])
    assert notes == 3 and clips == 3
    assert any(e["plugin"] == "Fruity Limiter" and e["track"] == 1 for e in effects)
    assert compact["automation"][0]["target"] == {"mixer": 0, "param": "vol"}
    assert compact["instruments"][0]["plugin"] == "3x Osc"
    assert flp.serialize(flp.parse(raw)) == raw
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == original_hash
    result.update(project=str(out), sha256=hashlib.sha256(raw).hexdigest(), warnings=warnings,
                  note_count=notes, clip_count=clips, effects=effects,
                  song_mode_event9=[int.from_bytes(e.data, "little") for e in flp.parse(raw).events if e.id == 9],
                  native_playback_and_render="NOT TESTED")
    result["checks"].update(factory_plugin_from_database=True, notes_written=True,
        audio_clip_written=True, playlist_clips_written=True, mixer_effect_written=True,
        automation_written=True, source_audio_unchanged=True, new_project_byte_roundtrip=True)
    # The codec and full JSON writer are different preservation promises.
    # Probe both default-template and explicit original-base reconstruction.
    rebuilds = []
    native = Path("C:/Image-Line/FL Studio/Projects/MCP-Setup-Test-20261020/MCP-Setup-Test-20261020.flp")
    for label, source in (("generated", out), ("native", native)):
        for explicit_base in (False, True):
            full = project.export(str(source))
            if explicit_base:
                full["base"] = str(source)
                full.setdefault("mixer", {})["from"] = str(source)
            rebuilt, notices = writer.build(full)
            rebuilds.append({"fixture": label, "explicit_original_base_and_mixer": explicit_base,
                             "byte_identical": rebuilt == source.read_bytes(),
                             "input_bytes": source.stat().st_size, "output_bytes": len(rebuilt),
                             "warnings": notices})
    result["full_json_rebuild_probes"] = rebuilds
    if not all(r["byte_identical"] for r in rebuilds if r["explicit_original_base_and_mixer"]):
        raise AssertionError("Explicit original-base preservation failed")
    (SOURCE / "offline-acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (SOURCE / "offline-acceptance-readback.json").write_text(json.dumps(readback, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
