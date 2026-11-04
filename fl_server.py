"""This PC's tested full-duplex FL bridge; runtime never uses keyboard automation."""
from pathlib import Path
import os
from fl_studio_mcp import pyscript_gen
from fl_studio_mcp import server

DISABLED_TOOLS = {
    "fl_write_piano_roll_notes", "fl_quantize_pattern",
    "fl_write_raga_chords", "fl_write_raga_melody",
    "fl_analyze_audio", "fl_extract_melody",
}

server.SERVER_INSTRUCTIONS = """FL26.1.7 live MIDI control on this PC.
Call fl_ping before writes. loopMIDI must be running. RX is one full-duplex bus:
RX input enabled, FLStudioMCP controller, port42; RX output42; TX output unassigned.
Channel indices are global, independent of the Channel Rack's selected group.
Use snapshots, fresh readback and rollback for supported live mix/plugin edits.
Tempo/transport changes require explicit restoration; dry-run is not universal.
Use fl_workbench.create_song/read_composition/write_composition for notes, patterns,
plugin/preset insertion, Playlist and automation in fresh saved project copies.
Use fl_workbench rendering when interactive FL is saved/closed. Never promise
atomic hot replacement of unsaved live arrangement or background audio listening.
The four legacy keyboard-triggered piano-roll tools are disabled; no session
arming or GUI automation is required for the saved-file composition route.
Use audio_analyzer for local DSP and gemini_music only for requested cloud listening.
Plugin values are normalized; trust observed display strings, not guessed units.
Installed catalog entries are not proof of license or third-party compatibility.
"""

_upstream_build = server.build_server


def build_server():
    mcp = _upstream_build()
    mcp.disable(names=DISABLED_TOOLS, components={"tool"})
    return mcp


server.build_server = build_server

# FL26 pairs the scripted RX input with its same-named output on every launch.
# Use that normal full-duplex loopMIDI bus instead of fighting the auto-pairing.
# Wire direction tags and correlation IDs keep request echo out of responses.
os.environ.setdefault("FLSTUDIO_MCP_PORT_FROM_FL", os.environ.get("FLSTUDIO_MCP_PORT_TO_FL", "FLStudioMCP RX"))

fl_data = Path(os.environ.get("FLSTUDIO_MCP_DATA_DIR", "C:/Image-Line/FL Studio"))
pyscript_gen.PIANO_ROLL_SCRIPTS_DIR = str(fl_data / "Settings/Piano roll scripts")
os.environ.setdefault("FLSTUDIO_MCP_PLUGIN_DB", str(fl_data / "Presets/Plugin database/Installed"))
os.environ.setdefault("FLSTUDIO_MCP_PRESETS", str(fl_data / "Presets"))

if __name__ == "__main__":
    server.main()
