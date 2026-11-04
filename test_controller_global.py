"""Regression: an empty visible group must not hide the project's channels."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

from patch_controller_global import GLOBAL_APIS, transform

SCRIPT = Path(__file__).parent / "runtime/flstudio-mcp/fl_controller/FLStudioMCP/device_FLStudioMCP.py"


class GlobalChannelTests(unittest.TestCase):
    def test_hidden_channel_read_and_write_use_global_index(self):
        source = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        selected = {"_channel_list_entry", "_h_channel_list", "_h_channel_set_pan"}
        subset = ast.Module(body=[n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in selected], type_ignores=[])
        api = Mock()
        api.channelCount.side_effect = lambda globalCount=0: 1 if globalCount else 0
        def global_read(index, useGlobalIndex=False):
            if not useGlobalIndex:
                raise IndexError("No channels in the visible Audio group")
            return "Hidden synth"
        api.getChannelName.side_effect = global_read
        api.getChannelPan.return_value = .25
        api.getChannelVolume.return_value = .8
        api.isChannelMuted.return_value = False
        api.isChannelSolo.return_value = False
        namespace = {"channels": api, "_truncate_name": lambda name:(name,False),
                     "_vol_out": lambda value:{"vol_norm":value}, "_clamp_pan": lambda value:value,
                     "_paginate": lambda total,start,entry,key:{key:[entry(i) for i in range(start,total)]}}
        exec(compile(subset, str(SCRIPT), "exec"), namespace)
        self.assertEqual(namespace["_h_channel_list"]({})["channels"][0]["name"], "Hidden synth")
        namespace["_h_channel_set_pan"]({"channel":0,"value":.25})
        api.setChannelPan.assert_called_once_with(0, .25, useGlobalIndex=True)

    def test_every_group_sensitive_call_is_global_and_patch_is_idempotent(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(transform(text), text)
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "channels":
                continue
            if node.func.attr in GLOBAL_APIS:
                self.assertTrue(any(k.arg == "useGlobalIndex" and isinstance(k.value, ast.Constant) and k.value.value is True
                                    for k in node.keywords), ast.unparse(node))
            if node.func.attr == "channelCount":
                self.assertEqual(ast.literal_eval(node.args[0]), 1)


if __name__ == "__main__":
    unittest.main()
