import unittest
from unittest.mock import Mock, patch
from fl_studio_mcp import connection
from fl_studio_mcp import protocol
import mido


class BridgeRecoveryTests(unittest.TestCase):
    def test_failed_open_does_not_poison_future_connection(self):
        failed = Mock()
        failed.open.side_effect = RuntimeError("MIDI service stopped")
        healthy = Mock()
        connection._bridge = None
        with patch.dict("os.environ", {"FLSTUDIO_MCP_TRANSPORT": "direct"}):
            with patch.object(connection, "FLBridge", side_effect=[failed, healthy]):
                with self.assertRaises(RuntimeError):
                    connection.get_bridge()
                self.assertIsNone(connection._bridge)
                failed.close.assert_called_once()
                self.assertIs(connection.get_bridge(), healthy)
                healthy.check_alive.assert_called_once()
        connection._bridge = None

    def test_full_duplex_request_echo_cannot_satisfy_response_waiter(self):
        bridge = connection.FLBridge(port_to_fl="FLStudioMCP RX", port_from_fl="FLStudioMCP RX")
        slot = connection._Slot()
        bridge._pending["abcdefgh"] = slot
        request = protocol.encode_message(protocol.DIR_REQUEST, "abcdefgh", protocol.make_request("get_tempo"))
        with patch.object(connection.logger, "warning") as warning:
            bridge._on_midi(mido.Message("sysex", data=request))
            self.assertFalse(slot.event.is_set())
            warning.assert_not_called()
        response = protocol.encode_message(protocol.DIR_RESPONSE, "abcdefgh", {"v":2,"ok":True,"data":{"bpm":120}})
        bridge._on_midi(mido.Message("sysex", data=response))
        self.assertTrue(slot.event.is_set())
        self.assertEqual(slot.payload["data"], {"bpm":120})


if __name__ == "__main__":
    unittest.main()
