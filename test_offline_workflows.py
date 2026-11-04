import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave
from unittest.mock import patch

import flp_bytes as wire
import workbench_server as w


def event(eid, data):
    return bytes([eid]) + (wire.varint(len(data)) if eid >= 192 else b"") + data


def project_bytes(sample="sample.wav", quirky=False):
    # Include opaque bytes and two different Playlist lengths to catch partial models.
    body = (event(199, b"25.1.5.4976\0") + event(156, struct.pack("<I", 120000))
            + (event(172, b"\x01") if quirky else b"")
            + event(64, b"\0\0") + event(196, (sample + "\0").encode("utf-16-le"))
            + event(238, bytes(range(256)))
            + event(233, b"A" * 80) + event(233, b"B" * 100))
    return b"FLhd" + struct.pack("<IHHH", 6, 0, 1, 96) + b"FLdt" + struct.pack("<I", len(body)) + body


def wav(path, gain=1.0):
    samples = [round(gain * 6000 * math.sin(i * 2 * math.pi * 220 / 16000)) for i in range(32000)]
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(struct.pack("<" + "h" * len(samples), *samples))


class WireTests(unittest.TestCase):
    def test_tempo_and_relink_preserve_all_unrelated_events(self):
        for quirky in (False, True):
            raw = project_bytes(quirky=quirky)
            revised = wire.change_tempo(raw, 99.125)
            revised = wire.relink_samples(revised, {"sample.wav": "C:/long/音楽/" + "x" * 150 + ".wav"})
            before, after = wire.parse(raw)[1], wire.parse(revised)[1]
            self.assertEqual(len(before), len(after))
            for a, b in zip(before, after):
                if a.id not in (156, 196):
                    self.assertEqual(raw[a.start:a.end], revised[b.start:b.end])
            self.assertEqual(wire.payloads(revised, 156), [struct.pack("<I", 99125)])
            self.assertIn("音楽", wire.sample_references(revised)[0])
            self.assertEqual([len(p) for p in wire.payloads(revised, 233)], [80, 100])

    def test_malformed_files_fail_closed(self):
        raw = project_bytes()
        for bad in (b"", raw[:-1], raw + b"trailing", raw[:18] + b"\xff\xff\xff\x7f" + raw[22:]):
            with self.assertRaises(ValueError):
                wire.change_tempo(bad, 100)
        with self.assertRaises(ValueError):
            wire.change_tempo(raw, float("nan"))


class OfflineWorkflowTests(unittest.TestCase):
    def test_portable_restore_works_after_original_media_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "sample.wav"
            original.write_bytes(b"known media bytes")
            src = root / "original.flp"
            src.write_bytes(project_bytes())
            with patch.object(w, "JOBS", root / "jobs"):
                checkpoint = w.checkpoint_project(str(src))
                original.unlink()
                restored = root / "portable.flp"
                result = w.restore_portable_checkpoint(checkpoint["manifest"], str(restored))
                self.assertEqual(result["assets_restored"], 1)
                self.assertEqual(result["missing_at_checkpoint"], [])
                refs = wire.sample_references(restored.read_bytes())
                self.assertEqual(Path(refs[0]).read_bytes(), b"known media bytes")
                self.assertEqual(src.read_bytes(), project_bytes())
                self.assertEqual(w.project_preflight(str(restored))["missing_samples"], [])
                with self.assertRaises(ValueError):
                    w.restore_portable_checkpoint(checkpoint["manifest"], str(restored))
                manifest = json.loads(Path(checkpoint["manifest"]).read_text())
                Path(manifest["assets"][0]["copy"]).write_bytes(b"corrupt")
                with self.assertRaises(ValueError):
                    w.restore_portable_checkpoint(checkpoint["manifest"], str(root / "bad.flp"))
                self.assertFalse((root / "bad.flp").exists())
                self.assertFalse((root / "bad.assets").exists())

    def test_missing_media_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "missing.flp"
            src.write_bytes(project_bytes("absent.wav"))
            with patch.object(w, "JOBS", root / "jobs"):
                backup = w.checkpoint_project(str(src))
                self.assertEqual(backup["missing_samples"], ["absent.wav"])
                result = w.restore_portable_checkpoint(backup["manifest"], str(root / "restored.flp"))
                self.assertEqual(result["missing_at_checkpoint"], ["absent.wav"])

    def test_midi_export_and_invalid_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "notes.mid"
            result = w.export_note_midi(str(out), [
                {"pitch": 60, "start": 0, "duration": 1},
                {"pitch": 64, "start": 1, "duration": 1, "velocity": 80}], tempo=120)
            data = out.read_bytes()
            self.assertEqual(data[:14], b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480))
            self.assertEqual(struct.unpack_from(">I", data, 18)[0], len(data) - 22)
            self.assertIn(b"\x90\x3c\x64", data)
            self.assertEqual(result["duration_seconds"], 1)
            self.assertFalse(result["live_notes_written"])
            with self.assertRaises(ValueError):
                w.export_note_midi(str(out), [{"pitch": 60, "start": 0, "duration": 1}])
            for note in ({"pitch": 128, "start": 0, "duration": 1},
                         {"pitch": 60, "start": float("nan"), "duration": 1},
                         {"pitch": True, "start": 0, "duration": 1}):
                with self.assertRaises(ValueError):
                    w.export_note_midi(str(root / "bad.mid"), [note])
            self.assertFalse((root / "bad.mid").exists())

    def test_parameter_lookup_requires_identity_and_unambiguous_observation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(w, "ROOT", Path(tmp)):
            profile = w.save_parameter_map("Test EQ", "1.2", 3, "Frequency", [
                {"normalized": 0.25, "display": "500 Hz"},
                {"normalized": 0.50, "display": "2 kHz"}])
            pid = profile["profile_id"]
            result = w.lookup_parameter_setting(pid, "Test EQ", "1.2", "500 Hz")
            self.assertEqual(result["normalized"], .25)
            self.assertFalse(result["applied"])
            for version, display in (("2.0", "500 Hz"), ("1.2", "750 Hz")):
                with self.assertRaises(ValueError):
                    w.lookup_parameter_setting(pid, "Test EQ", version, display)
            ambiguous = w.save_parameter_map("Test", "1", 0, "P", [
                {"normalized": .1, "display": "Same"}, {"normalized": .2, "display": "Same"}])
            with self.assertRaises(ValueError):
                w.lookup_parameter_setting(ambiguous["profile_id"], "Test", "1", "Same")
            with self.assertRaises(ValueError):
                w.lookup_parameter_setting("../../bad", "Test", "1", "Same")

    def test_real_audio_comparison_excerpt_and_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b, silence = root / "before.wav", root / "after.wav", root / "silent.wav"
            wav(a)
            wav(b, .5)
            wav(silence, 0)
            report = w.compare_mix_exports(str(a), str(b))
            self.assertAlmostEqual(report["after_minus_before"]["input_i"], -6.02, delta=.1)
            self.assertAlmostEqual(report["after_minus_before"]["input_tp"], -6.02, delta=.1)
            self.assertEqual(report["warnings"], [])
            self.assertTrue(w.validate_audio_export(str(a), 2)["accepted"])
            self.assertFalse(w.validate_audio_export(str(a), 8)["accepted"])
            self.assertFalse(w.validate_audio_export(str(silence), 2)["accepted"])
            original_hash = w.digest(a)
            excerpt = w.prepare_listening_excerpt(str(a), str(root / "excerpt.wav"), .5, 1)
            self.assertAlmostEqual(excerpt["duration_seconds"], 1, places=2)
            self.assertEqual((excerpt["sample_rate"], excerpt["channels"]), (24000, 2))
            self.assertLess(excerpt["bytes"], 12 * 1024 * 1024)
            self.assertFalse(excerpt["cloud_uploaded"])
            self.assertEqual(w.digest(a), original_hash)
            with self.assertRaises(ValueError):
                w.prepare_listening_excerpt(str(a), str(root / "excerpt.wav"), 0, 1)

    def test_renderer_does_not_launch_when_process_check_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, exe = root / "song.flp", root / "FL64.exe"
            src.write_bytes(project_bytes())
            exe.write_bytes(b"test")
            with patch.object(w, "FL", exe), patch.object(w.subprocess, "run") as run, \
                    patch.object(w.subprocess, "Popen") as launch:
                run.return_value.returncode = 1
                with self.assertRaises(ValueError):
                    w.render_saved_project(str(src))
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
