import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import gemini_server as server


class ListeningSafetyTests(unittest.TestCase):
    def test_local_validation_never_calls_google(self):
        with patch.object(server, "request_api") as api:
            with self.assertRaises(ValueError):
                server.analyze_audio("relative.wav")
            with tempfile.TemporaryDirectory() as directory:
                unsupported = Path(directory) / "secret.txt"
                unsupported.write_text("private")
                with self.assertRaises(ValueError):
                    server.analyze_audio(str(unsupported))
                oversized = Path(directory) / "large.wav"
                with oversized.open("wb") as out:
                    out.truncate(server.MAX_BYTES + 1)
                with self.assertRaises(ValueError):
                    server.analyze_audio(str(oversized))
            api.assert_not_called()

    def test_provider_error_is_redacted_and_not_retried(self):
        failure = HTTPError("https://example.invalid/secret", 429, "secret", {}, io.BytesIO(b"secret"))
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only-placeholder"}):
            with patch("urllib.request.urlopen", side_effect=failure) as network:
                with self.assertRaisesRegex(RuntimeError, "Quota exhausted") as caught:
                    server.request_api("/models")
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(network.call_count, 1)

    def test_missing_key_never_calls_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("urllib.request.urlopen") as network:
                with self.assertRaisesRegex(ValueError, "missing"):
                    server.request_api("/models")
                network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
