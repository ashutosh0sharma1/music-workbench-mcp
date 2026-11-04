import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import workbench_server as w


class WorkbenchTests(unittest.TestCase):
    def test_playlist_payload_survives_writing_and_tempo_revision(self):
        # Inspect actual wire bytes, not the permissive core-field reader:
        # PyFLP's 32/60-byte model formerly truncated 80-byte modern records.
        fixture = w.ROOT / "runtime/synthetic-test.wav"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for count in (1, 2, 3):
                project = root / f"clips-{count}.flp"
                revised = root / f"revised-{count}.flp"
                w.arrange_audio_project(str(project), [str(fixture)] * count, stagger_bars=4)
                payloads = lambda p: [d for eid, _, d in w.flp._flp_events(p)[1] if eid == 233]
                before = payloads(project)
                self.assertEqual([len(d) for d in before], [80 * count])
                self.assertEqual(w.flp.read_playlist(str(project))["clip_count"], count)
                w.revise_saved_project(str(project), str(revised), 100)
                self.assertEqual(payloads(revised), before)

    def test_arrange_checkpoint_restore_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = w.ROOT / "runtime/synthetic-test.wav"
            with patch.object(w, "JOBS", root / "checkpoints"):
                project = root / "song.flp"
                w.arrange_audio_project(str(project), [str(fixture)], tempo=120)
                playlist = w.flp.read_playlist(str(project))
                self.assertEqual(playlist["clip_count"], 1)
                self.assertAlmostEqual(playlist["clips"][0]["length_sec"], 8.0)
                original = w.digest(project)
                checkpoint = w.checkpoint_project(str(project), [str(fixture)])
                restored = root / "restored.flp"
                w.restore_checkpoint(checkpoint["manifest"], str(restored))
                self.assertEqual(original, w.digest(restored))
                with self.assertRaises(ValueError):
                    w.arrange_audio_project(str(project), [str(fixture)])
                w.revise_saved_project(str(project), str(root / "revised.flp"), 100)
                self.assertEqual(original, w.digest(project))
                manifest = json.loads(Path(checkpoint["manifest"]).read_text())
                Path(manifest["project"]).write_bytes(b"corrupt")
                with self.assertRaises(ValueError):
                    w.restore_checkpoint(checkpoint["manifest"], str(root / "bad.flp"))

    def test_invalid_jobs_and_paths(self):
        with self.assertRaises(ValueError):
            w.render_status("../../something")
        with self.assertRaises(ValueError):
            w.render_saved_project("relative.flp")

    def test_template_preserves_bytes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, out = root / "template.flp", root / "copy.flp"
            src.write_bytes(b"opaque native plugin data")
            w.create_from_plugin_template(str(src), str(out))
            self.assertEqual(src.read_bytes(), out.read_bytes())
            with self.assertRaises(ValueError):
                w.create_from_plugin_template(str(src), str(out))


if __name__ == "__main__":
    unittest.main()
