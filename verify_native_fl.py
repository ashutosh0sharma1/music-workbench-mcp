"""Real FL CLI acceptance on synthetic projects only. No GUI automation/cloud."""
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import time
import wave

import workbench_server as w


def pcm(path):
    result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                             "-ac", "1", "-ar", "44100", "-f", "f32le", "-"],
                            capture_output=True, timeout=30, check=True)
    return struct.unpack("<" + "f" * (len(result.stdout) // 4), result.stdout)


def rms(values, start, end):
    data = values[round(start * 44100):round(end * 44100)]
    return math.sqrt(sum(x*x for x in data) / max(1, len(data)))


def main():
    folder = w.ROOT / "runtime" / ("native-acceptance-20261022-" + str(time.time_ns()))
    folder.mkdir(parents=True)
    tone = folder / "tone.wav"
    with wave.open(str(tone), "wb") as out:
        out.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(struct.pack("<h", round(6000 * math.sin(2*math.pi*220*i/44100))) for i in range(8*44100)))
    common = {"format": "compact", "project": {"bpm": 120, "ppq": 96, "song_mode": True},
              "instruments": [], "samples": [], "patterns": [], "arrangements": [{"name": "Song", "clips": []}]}
    report = {"fl_executable": str(w.FL), "executable_sha256": w.digest(w.FL),
              "computer_use": False, "cloud": False, "checks": {}, "renders": {}}
    def save():
        (folder / "receipt.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    def render(name, project, duration):
        job = w.render_saved_project(str(project), 90, duration)
        while True:
            status = w.render_status(job["job_id"])
            if status["state"] not in ("queued", "running"):
                break
            if time.time() - job["created"] > 110:
                raise RuntimeError("Acceptance worker did not settle")
            time.sleep(.5)
        report["renders"][name] = status
        save()
        if status["state"] != "completed":
            raise AssertionError(status)
        print(json.dumps({"phase": name, "state": "completed", "duration": status["qa"]["duration_seconds"]}), flush=True)
        return pcm(status["output"])
    def build(name, doc):
        p = folder / (name + ".flp")
        w.write_composition(str(p), copy.deepcopy(doc))
        return p
    audio = copy.deepcopy(common)
    audio["samples"] = [{"id": "tone", "kind": "audio", "path": str(tone), "mixer": 1}]
    audio["arrangements"][0]["clips"] = [[1, 1, 1, 0, 1536, "s0"]]
    audio_project = build("audio", audio)
    baseline = render("audio", audio_project, 8)
    report["checks"]["audio_clip_audible"] = rms(baseline, 1, 2) > .01
    shifted = copy.deepcopy(audio)
    shifted["arrangements"][0]["clips"][0][1] = 3
    delayed = render("playlist-offset", build("playlist-offset", shifted), 12)
    report["checks"]["playlist_placement"] = rms(delayed, 0, 3.8) < 1e-5 and rms(delayed, 4.2, 5.2) > .01
    faded = copy.deepcopy(audio)
    faded["automation"] = [{"id": "fade", "kind": "automation", "target": {"mixer": 0, "param": "vol"},
                            "points": [[0, .8], [8, .8], [16, 0]]}]
    faded["arrangements"][0]["clips"].append([2, 1, 1, 0, 1536, "a0"])
    fade_pcm = render("automation", build("automation", faded), 8)
    report["checks"]["automation_changes_audio"] = rms(fade_pcm, 7, 7.9) < rms(fade_pcm, 1, 2) * .25
    notes = copy.deepcopy(common)
    notes["instruments"] = [{"id": "osc", "kind": "plugin", "plugin": "3x Osc", "mixer": 1}]
    notes["patterns"] = [{"id": "p1", "notes": [[1, 1, 0, 192, "C4", 90, "osc"],
                                                  [2, 1, 0, 192, "G4", 90, "osc"]]}]
    notes["arrangements"][0]["clips"] = [[1, 1, 1, 0, 1536, "p1"]]
    dry = render("notes", build("notes", notes), 8)
    report["checks"]["plugin_notes_and_patterns"] = rms(dry, .1, .8) > .005 and rms(dry, 2.5, 3.5) > .005
    fx = copy.deepcopy(notes)
    fx["mixer"] = {"effects": [{"track": 1, "slot": 0, "plugin": "Fruity Reeverb 2"}]}
    wet = render("effect", build("effect", fx), 8)
    report["checks"]["effect_changes_audio"] = sum(abs(a-b) for a,b in zip(dry,wet)) / len(dry) > 1e-5
    presets = w.plugin_preset_catalog("3x Osc", limit=1)
    if presets["presets"]:
        preset = presets["presets"][0]
        chosen = copy.deepcopy(notes)
        chosen["instruments"][0]["preset"] = preset["id"]
        preset_pcm = render("preset", build("preset", chosen), 8)
        report["checks"]["native_preset_audible"] = rms(preset_pcm, .1, .8) > .001
        report["preset"] = preset
    checkpoint = w.checkpoint_project(str(audio_project))
    tone.rename(tone.with_suffix(".held"))
    restored = folder / "restored.flp"
    w.restore_portable_checkpoint(checkpoint["manifest"], str(restored))
    restored_pcm = render("portable-restore", restored, 8)
    difference = max(abs(a-b) for a,b in zip(baseline,restored_pcm))
    report["checks"]["restore_after_source_removed"] = len(baseline) == len(restored_pcm) and difference < 1e-6
    report["restore_max_sample_difference"] = difference
    # Byte-preservation acceptance is separate from native audio acceptance.
    doc = w.read_composition(str(audio_project))["composition"]
    rebuilt = folder / "rebuilt.flp"
    w.write_composition(str(rebuilt), doc)
    report["checks"]["full_export_rebuild_byte_identical"] = w.digest(rebuilt) == w.digest(audio_project)
    report["passed"] = all(report["checks"].values())
    save()
    if report["passed"]:
        (w.ROOT / "runtime/native-acceptance-current.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"receipt": str(folder / "receipt.json"), "passed": report["passed"], "checks": report["checks"]}), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
