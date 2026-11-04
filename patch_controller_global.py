"""Apply the FL33+ global-channel API flags without rewriting unrelated code."""
import ast
import difflib
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "runtime/flstudio-mcp/fl_controller/FLStudioMCP/device_FLStudioMCP.py"
INSTALLED = Path("C:/Image-Line/FL Studio/Settings/Hardware/FLStudioMCP/device_FLStudioMCP.py")
GLOBAL_APIS = {
    "getTargetFxTrack", "getChannelName", "getChannelPan", "isChannelMuted",
    "isChannelSolo", "getChannelVolume", "setChannelVolume", "setChannelPan",
    "muteChannel", "soloChannel", "getChannelColor", "setChannelColor",
    "selectOneChannel", "getGridBit", "isGridBitAssigned",
}


def transform(source):
    lines = source.splitlines(keepends=True)
    offsets, offset = [], 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    edits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "channels":
            continue
        name = node.func.attr
        if name == "channelCount" and not node.args:
            addition = "1"
        elif name in GLOBAL_APIS and not any(k.arg == "useGlobalIndex" for k in node.keywords):
            # isGridBitAssigned takes a channel, not a step position. Its old
            # second argument accidentally toggled useGlobalIndex by step.
            if name == "isGridBitAssigned":
                start = offsets[node.lineno - 1] + node.col_offset
                end = offsets[node.end_lineno - 1] + node.end_col_offset
                edits.append((start, end, "channels.isGridBitAssigned(ch, useGlobalIndex=True)"))
                continue
            addition = ", useGlobalIndex=True"
        else:
            continue
        end = offsets[node.end_lineno - 1] + node.end_col_offset - 1
        edits.append((end, end, addition))
    for start, end, text in sorted(edits, reverse=True):
        source = source[:start] + text + source[end:]
    return source.replace('"probe-v16"', '"global-v17"')


if __name__ == "__main__":
    original = SOURCE.read_text(encoding="utf-8")
    installed = INSTALLED.read_text(encoding="utf-8")
    if original != installed:
        raise SystemExit("Controller sources differ; inspect before patching")
    updated = transform(original)
    if updated == original:
        raise SystemExit("Global-channel patch already present; preserved reconstruction patch")
    compile(updated, str(SOURCE), "exec")
    backup = ROOT / "runtime" / f"controller-before-global-{time.time_ns()}.py"
    shutil.copy2(INSTALLED, backup)
    (ROOT / "fl-global-channels.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(True), updated.splitlines(True),
        fromfile="a/fl_controller/FLStudioMCP/device_FLStudioMCP.py",
        tofile="b/fl_controller/FLStudioMCP/device_FLStudioMCP.py")), encoding="utf-8", newline="\n")
    SOURCE.write_text(updated, encoding="utf-8", newline="\n")
    INSTALLED.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Patched both controllers; backup: {backup}")
