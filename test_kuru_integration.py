"""Meaningful offline acceptance for preservation, failure boundaries and recovery."""
import copy
import json
from pathlib import Path
import tempfile
import os
import time
import unittest
from unittest.mock import patch, Mock

import workbench_server as w
import kuru_backend


class IntegrationTests(unittest.TestCase):
    def composition(self):
        return {"format": "compact", "project": {"bpm": 120, "ppq": 96},
                "instruments": [{"id": "osc", "kind": "plugin", "plugin": "3x Osc"}],
                "samples": [], "patterns": [{"id": "p1", "notes": [[1,1,0,384,"C4",80,"osc"]]}],
                "arrangements": [{"name": "Song", "clips": [[1,1,1,0,384,"p1"]]}]}

    def test_saved_full_edit_preserves_unmapped_bytes_and_original(self):
        native = Path("C:/Image-Line/FL Studio/Projects/MCP-Setup-Test-20261020/MCP-Setup-Test-20261020.flp")
        before = w.digest(native)
        with tempfile.TemporaryDirectory() as tmp, patch.object(w, "JOBS", Path(tmp)/"jobs"):
            out = Path(tmp)/"copy.flp"
            doc = w.read_composition(str(native))["composition"]
            made = w.write_composition(str(out), doc)
            self.assertEqual(w.digest(out), before)
            self.assertEqual(w.digest(native), before)
            self.assertTrue(Path(made["checkpoint_manifest"]).is_file())
            with self.assertRaises(ValueError):
                w.write_composition(str(Path(tmp)/"compact.flp"), self.composition(), str(native))

    def test_warnings_and_overwrites_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/"new.flp"
            spec = self.composition()
            bad = copy.deepcopy(spec)
            bad["patterns"][0]["name"] = "Unmapped requested rename"
            with self.assertRaisesRegex(ValueError, "Unmapped changes"):
                w.write_composition(str(out), bad)
            self.assertFalse(out.exists())
            w.write_composition(str(out), spec)
            before = out.read_bytes()
            with self.assertRaises(ValueError):
                w.write_composition(str(out), spec)
            self.assertEqual(out.read_bytes(), before)
            bad = copy.deepcopy(spec)
            bad["project"]["bpm"] = float("nan")
            with self.assertRaises(ValueError):
                w.write_composition(str(Path(tmp)/"bad.flp"), bad)

    def test_adapter_blocks_live_escape(self):
        for action in ("subprocess.Popen", "socket.connect", "ctypes.dlopen", "winreg.SetValue"):
            with self.assertRaises(RuntimeError):
                kuru_backend.block_live(action, ())

    def test_convenient_song_uses_beats_and_refuses_invalid_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"song.flp"
            tracks = [{"plugin":"3x Osc", "start_beat":8, "notes":[
                {"pitch":60,"start":1.5,"duration":2}]}]
            result = w.create_song(str(path), tracks)
            self.assertEqual(result["expected_seconds_at_base_tempo"], 5.75)
            doc = w.read_composition(str(path))["composition"]
            self.assertEqual(doc["patterns"][0]["notes"][0]["at"], [1,2,48])
            self.assertEqual(doc["arrangements"][0]["clips"][0]["at"], [3,1,0])
            tracks[0]["notes"][0]["duration"] = float("nan")
            with self.assertRaises(ValueError):
                w.create_song(str(Path(tmp)/"bad.flp"), tracks)

    def test_library_restore_tamper_and_path_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(w, "JOBS", Path(tmp)/"jobs"):
            root = Path(tmp)
            library = root/"library"
            (library/"nested").mkdir(parents=True)
            (library/"nested"/"sample.bin").write_bytes(b"external instrument data")
            with self.assertRaises(ValueError):
                w.checkpoint_library(str(library), maximum_bytes=1)
            self.assertFalse((root/"jobs").exists())
            saved = w.checkpoint_library(str(library))
            (library/"nested"/"sample.bin").unlink()
            restored = root/"restored"
            w.restore_library(saved["manifest"], str(restored))
            self.assertEqual((restored/"nested"/"sample.bin").read_bytes(), b"external instrument data")
            manifest = Path(saved["manifest"])
            data = json.loads(manifest.read_text())
            data["files"][0]["relative"] = "../escape.bin"
            manifest.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                w.restore_library(str(manifest), str(root/"escape"))
            self.assertFalse((root/"escape").exists())

    def test_autosave_capture_rejects_incomplete_and_outside_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(w, "JOBS", Path(tmp)/"jobs"), \
                patch.dict(os.environ, {"FLSTUDIO_MCP_DATA_DIR": str(Path(tmp)/"data")}):
            directory = Path(tmp)/"data"/"Projects"/"Backup"
            directory.mkdir(parents=True)
            project = directory/"synthetic.flp"
            w.write_composition(str(project), self.composition())
            with self.assertRaises(ValueError):
                w.checkpoint_autosave(str(project))
            os.utime(project, (time.time()-10, time.time()-10))
            saved = w.checkpoint_autosave(str(project))
            self.assertEqual(saved["sha256"], w.digest(project))
            self.assertEqual(w.list_autosaves()["autosaves"][0]["path"], str(project))
            outside = Path(tmp)/"outside.flp"
            outside.write_bytes(project.read_bytes())
            os.utime(outside, (time.time()-10, time.time()-10))
            with self.assertRaises(ValueError):
                w.checkpoint_autosave(str(outside))

    def test_render_relinks_relative_media_and_serializes_jobs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(w, "JOBS", Path(tmp)/"jobs"):
            root = Path(tmp)
            tone = root/"take.wav"
            tone.write_bytes((w.ROOT/"runtime/synthetic-test.wav").read_bytes())
            spec = self.composition()
            spec["samples"] = [{"id":"take", "kind":"audio", "path":str(tone)}]
            spec["arrangements"][0]["clips"].append([2,1,1,0,1536,"s0"])
            project = root/"input.flp"
            w.write_composition(str(project), spec)
            project.write_bytes(w.flp_bytes.relink_samples(project.read_bytes(), {str(tone): "take.wav"}))
            run = Mock(return_value=Mock(returncode=0, stdout=""))
            with patch.object(w.subprocess, "run", run), patch.object(w.subprocess, "Popen") as launch:
                launch.return_value.pid = 123
                job = w.render_saved_project(str(project))
                actual = w.flp_bytes.sample_references((root/"jobs"/job["job_id"]/"input.flp").read_bytes())
                self.assertIn(str(tone), actual)
                with self.assertRaises(ValueError):
                    w.render_saved_project(str(project))
                self.assertEqual(launch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
