"""Non-destructive offline FL projects, checkpoints, render jobs and audio QA."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import struct

import flp_bytes
import kuru_backend

from fastmcp import FastMCP
from fl_studio_mcp.routes import pyflp_route as flp

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "runtime/workbench-jobs"
FL = Path(os.environ.get("FLSTUDIO_EXECUTABLE", "C:/Program Files/Image-Line/FL Studio 2026/FL64.exe"))
mcp = FastMCP("FL Workbench", instructions=(
    "FL Studio composition, Playlist, plugins/presets, automation, verified CLI rendering and recovery. "
    "File edits always make new files. Use read_composition then write_composition for full saved projects. "
    "Use compact Kuru composition for new songs. Original base/mixer are preserved and checkpointed. "
    "Unsaved FL changes are not included. Use checkpoints and rendered before/after "
    "audio measurements. Musical judgments and uncalibrated plugin units are estimates. "
    "Never use this server for GUI automation. Rendering requires FL closed and refuses concurrent sessions. "
    "Consult workbench_status for actual local factory acceptance; this is not proof for every third-party plugin."
))


@mcp.tool()
def workbench_status() -> dict:
    """Report installed backend/render paths and hash-bound native acceptance evidence.

    Does not launch FL, probe account/activation, click UI or send audio to cloud.
    """
    manifest_path = kuru_backend.BACKEND / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    receipt_path = ROOT / "runtime/native-acceptance-current.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else None
    current_hash = digest(FL) if FL.is_file() else None
    return {"executable": str(FL), "executable_exists": FL.is_file(),
            "backend_pin": manifest.get("pin") if manifest else None,
            "native_acceptance_matches_executable": bool(receipt and receipt.get("executable_sha256") == current_hash),
            "native_checks": receipt.get("checks", {}) if receipt else {},
            "native_evidence": str(receipt_path) if receipt else None,
            "live_session": "Separate MIDI bridge; fl_ping must succeed before live operations",
            "scope": "Factory synthetic fixture acceptance; third-party licenses/units and unsaved RAM recovery not guaranteed"}


@mcp.tool()
def create_from_plugin_template(template_path: str, output_path: str) -> dict:
    """Instantiate a saved native FLP containing desired plugins/presets into a NEW project.

    Preserves opaque VST state byte-for-byte. Not a tool for injecting arbitrary
    plugins into the running song. Save reusable instrument/effect templates in FL.
    """
    template = source(template_path, ".flp")
    out = fresh(output_path, ".flp")
    copy_new(template, out)
    return {"project": str(out), "sha256": digest(out), "template_sha256": digest(template)}


def source(path, suffix=None):
    p = Path(path)
    if not p.is_absolute() or not p.is_file():
        raise ValueError("An existing absolute file path is required")
    if suffix and p.suffix.lower() != suffix:
        raise ValueError(f"Expected {suffix}")
    return p.resolve()


def fresh(path, suffix):
    p = Path(path)
    if not p.is_absolute() or p.suffix.lower() != suffix or p.exists():
        raise ValueError(f"Use a new absolute {suffix} path; existing files are never overwritten")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_new(src, out):
    # Exclusive creation also enforces no-overwrite if another caller races us.
    with Path(out).open("xb") as destination, Path(src).open("rb") as original:
        shutil.copyfileobj(original, destination)


def write_json(path, value):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


@mcp.tool()
def inspect_saved_project(project_path: str) -> dict:
    """Read saved FLP structure and Playlist clips; never represents unsaved state."""
    p = source(project_path, ".flp")
    return {"sha256": digest(p), "info": flp.info(str(p)), "playlist": flp.read_playlist(str(p))}


@mcp.tool()
def read_composition(project_path: str) -> dict:
    """Read full saved notes/channels/Playlist/effects/automation, including opaque data.

    Edit this full object and pass it to write_composition. Bar math assumes 4/4;
    original event bytes are retained. This does not read an unsaved open session.
    """
    p = source(project_path, ".flp")
    return {"sha256": digest(p), "composition": kuru_backend.call("read", {"project_path": str(p)})}


@mcp.tool()
def write_composition(output_path: str, composition: dict, base_project: str | None = None) -> dict:
    """Write NEW saved notes/patterns/Playlist/plugin presets/effects/automation/routing.

    For a new song use Kuru compact JSON. For an existing song use read_composition's
    full object; original base/mixer are forced and checkpointed. Unsupported writer
    warnings are errors. Presets requiring live harvesting are refused. No FL GUI,
    running-session edit or native compatibility is implied; render and compare.
    """
    out = fresh(output_path, ".flp")
    base = base_project or composition.get("project", {}).get("source") or composition.get("base")
    backup = checkpoint_project(str(source(base, ".flp"))) if base else None
    result = kuru_backend.call("write", {"output_path": str(out), "composition": composition,
                                        "base_project": str(source(base, ".flp")) if base else None})
    result["checkpoint_manifest"] = backup["manifest"] if backup else None
    return result


@mcp.tool()
def create_song(output_path: str, tracks: list[dict], tempo: float = 120,
                automation: list[dict] | None = None) -> dict:
    """Convenient new-song creation using quarter-note beats, without FL GUI.

    Each track: {name, plugin, preset?, notes:[{pitch,start,duration,velocity?}],
    start_beat?:0, effects?:[{plugin,preset?}]} OR {name,audio_path,start_beat?:0}.
    Each receives its own mixer track. Note starts are relative to that track's
    pattern; track start_beat places the clip on the Playlist. Automation entries:
    {target:{mixer:0,param:'vol'},points:[[beat,normalized],...]} over the full song.
    Uses 96 PPQ and 4/4 bar math. Creates a NEW project, checkpoint/render afterward.
    """
    if not 1 <= len(tracks) <= 64 or not math.isfinite(tempo) or not 10 <= tempo <= 999:
        raise ValueError("Use 1..64 tracks and tempo 10..999")
    def position(beats):
        if type(beats) not in (int, float) or not math.isfinite(beats) or not 0 <= beats <= 1000000:
            raise ValueError("Beat positions must be finite, nonnegative and bounded")
        ticks = round(beats * 96)
        return [ticks // 384 + 1, (ticks % 384) // 96 + 1, ticks % 96]
    doc = {"format": "compact", "project": {"bpm": tempo, "ppq": 96, "song_mode": True},
           "instruments": [], "samples": [], "patterns": [], "automation": [],
           "arrangements": [{"name": "Song", "clips": []}], "mixer": {"effects": []}}
    end = 0
    for i, track in enumerate(tracks):
        start = track.get("start_beat", 0)
        at = position(start)
        name = str(track.get("name", f"Track {i+1}"))[:100]
        if "audio_path" in track:
            if "plugin" in track or track.get("notes"):
                raise ValueError("Audio tracks cannot also contain a plugin or notes")
            path = source(track["audio_path"])
            length = max(1, round(audio_metadata(path)["duration_seconds"] * tempo / 60 * 96))
            src = "s" + str(len(doc["samples"]))
            doc["samples"].append({"id": f"audio{i}", "name": name, "kind": "audio",
                                   "path": str(path), "mixer": i+1})
        else:
            plugin = track.get("plugin")
            if not isinstance(plugin, str) or not plugin.strip() or not track.get("notes"):
                raise ValueError("Instrument tracks require a plugin name and notes")
            instrument = {"id": f"instrument{i}", "kind": "plugin", "plugin": plugin, "name": name, "mixer": i+1}
            if track.get("preset"):
                instrument["preset"] = track["preset"]
            doc["instruments"].append(instrument)
            notes = []
            length = 0
            for note in track["notes"]:
                pitch, velocity = note["pitch"], note.get("velocity", 100)
                duration = note["duration"]
                if (type(pitch) is not int or not 0 <= pitch <= 127 or type(velocity) is not int
                        or not 1 <= velocity <= 127 or type(duration) not in (int, float)
                        or not math.isfinite(duration) or not 0 < duration <= 1000000):
                    raise ValueError("Invalid pitch, velocity or duration")
                when = note.get("start", 0)
                note_at = position(when)
                ticks = round(duration * 96)
                if ticks < 1:
                    raise ValueError("Note shorter than one tick")
                notes.append([*note_at, ticks, pitch, velocity, instrument["id"]])
                length = max(length, round(when * 96) + ticks)
            src = f"p{i+1}"
            doc["patterns"].append({"id": src, "notes": notes})
        doc["arrangements"][0]["clips"].append([i+1, *at, length, src])
        end = max(end, round(start*96) + length)
        for slot, effect in enumerate(track.get("effects", [])):
            if slot >= 10:
                raise ValueError("At most 10 effects per track")
            doc["mixer"]["effects"].append({**effect, "track": i+1, "slot": slot})
    for i, item in enumerate(automation or []):
        points = item["points"]
        if not points or any(len(point) < 2 or type(point[1]) not in (int,float)
                             or not math.isfinite(point[1]) or not 0 <= point[1] <= 1 for point in points):
            raise ValueError("Automation needs beat/value points with normalized values 0..1")
        for point in points:
            position(point[0])
            end = max(end, round(point[0]*96))
        doc["automation"].append({"id": f"automation{i}", "kind": "automation", "target": item["target"], "points": points})
    for i in range(len(doc["automation"])):
        doc["arrangements"][0]["clips"].append([len(tracks)+i+1, 1, 1, 0, end, f"a{i}"])
    result = write_composition(output_path, doc)
    result["expected_seconds_at_base_tempo"] = end / 96 * 60 / tempo
    return result


@mcp.tool()
def plugin_catalog(query: str = "", limit: int = 100) -> dict:
    """List installed plugin database identities, formats and preset/map availability.

    Metadata only; presence is not proof of loading, license or native preset recall.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be 1..1000")
    return kuru_backend.call("plugins", {"query": query, "limit": limit})


@mcp.tool()
def plugin_preset_catalog(plugin: str, query: str = "", limit: int = 100) -> dict:
    """Find installed .fst/vendor presets supported by the pinned file backend.

    Use returned IDs in composition instruments/effects. Does not load or harvest
    a plugin. Some vendor preset families need a previously saved native instance.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be 1..1000")
    return kuru_backend.call("presets", {"plugin": plugin, "query": query, "limit": limit})


@mcp.tool()
def plugin_parameter_catalog(plugin: str, query: str = "", limit: int = 100) -> dict:
    """Read upstream observed parameter maps, not calibrated installed-version proof.

    Actual live identity/version and value readback must match before applying.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be 1..1000")
    return kuru_backend.call("params", {"plugin": plugin, "query": query, "limit": limit})


@mcp.tool()
def arrange_audio_project(output_path: str, audio_paths: list[str], tempo: float = 120,
                          stagger_bars: int = 0) -> dict:
    """Create a NEW FLP with samples on native Playlist audio tracks. Zero stagger layers stems.

    This creates a separate project; it does not insert clips into the running project.
    """
    if not 10 <= tempo <= 999 or not 0 <= stagger_bars <= 10000 or not 1 <= len(audio_paths) <= 64:
        raise ValueError("Invalid tempo/stagger or sample count (1..64)")
    samples = [source(p).as_posix() for p in audio_paths]
    ppq = 96
    doc = {"format": "compact", "project": {"bpm": tempo, "ppq": ppq, "song_mode": True},
           "instruments": [], "samples": [], "patterns": [], "arrangements": [{"name": "Song", "clips": []}]}
    for i, path in enumerate(samples):
        seconds = audio_metadata(path)["duration_seconds"]
        doc["samples"].append({"id": f"audio{i}", "kind": "audio", "name": Path(path).stem,
                               "path": path, "mixer": min(i + 1, 125)})
        doc["arrangements"][0]["clips"].append([i + 1, 1 + i * stagger_bars, 1, 0,
                                               round(seconds * tempo / 60 * ppq), f"s{i}"])
    result = write_composition(output_path, doc)
    return {"composition": result, **inspect_saved_project(output_path)}


@mcp.tool()
def revise_saved_project(project_path: str, output_path: str, tempo: float) -> dict:
    """Write tempo changes to a separate FLP, keeping the original untouched."""
    src = source(project_path, ".flp")
    out = fresh(output_path, ".flp")
    if not 10 <= tempo <= 999:
        raise ValueError("Invalid tempo")
    # Avoid reserializing opaque plugin, automation and modern Playlist data.
    result = flp_bytes.change_tempo(src.read_bytes(), tempo)
    with out.open("xb") as stream:
        stream.write(result)
    return {"source_sha256": digest(src), "result": inspect_saved_project(str(out))}


@mcp.tool()
def checkpoint_project(project_path: str, additional_assets: list[str] | None = None) -> dict:
    """Byte-exact saved FLP checkpoint plus discovered sample files and supplied assets.

    Embedded plugin state stays in the FLP. External plugin libraries/licenses and
    unsaved edits are not captured. Manifest reports missing referenced samples.
    """
    src = source(project_path, ".flp")
    assets = set(str(source(p)) for p in (additional_assets or []))
    missing = []
    references = {}
    # Discover references and store exactly the same snapshot, even if the source
    # is saved again while assets are being collected.
    raw_snapshot = src.read_bytes()
    for sample in flp_bytes.sample_references(raw_snapshot):
        p = Path(sample)
        resolved = (p if p.is_absolute() else src.parent / p).resolve()
        if resolved.is_file():
            assets.add(str(resolved))
            references.setdefault(str(resolved), []).append(sample)
        else:
            missing.append(sample)
    folder = JOBS / ("checkpoint-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    copy = folder / "project.flp"
    copy.write_bytes(raw_snapshot)
    entries = []
    for i, item in enumerate(sorted(assets)):
        asset = source(item)
        destination = folder / f"asset-{i}{asset.suffix}"
        shutil.copy2(asset, destination)
        entries.append({"original": str(asset), "copy": str(destination), "sha256": digest(destination),
                        "references": references.get(str(asset), [])})
    manifest = {"original": str(src), "project": str(copy), "sha256": digest(copy),
                "assets": entries, "missing_samples": missing,
                "scope": "Saved project and discovered media; not unsaved state or plugin installation"}
    write_json(folder / "manifest.json", manifest)
    return {"manifest": str(folder / "manifest.json"), **manifest}


@mcp.tool()
def restore_checkpoint(manifest_path: str, output_path: str) -> dict:
    """Verify checkpoint hashes and restore FLP to a NEW path. Never overwrites a song.

    Asset copies remain in checkpoint; FLP retains original references. Missing/moved
    originals are reported so they can be relinked before claiming a complete restore.
    """
    manifest_file = source(manifest_path, ".json")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    project = source(manifest["project"], ".flp")
    if project.parent != manifest_file.parent or digest(project) != manifest["sha256"]:
        raise ValueError("Checkpoint project integrity failure")
    relink = []
    for asset in manifest["assets"]:
        copy = source(asset["copy"])
        if copy.parent != manifest_file.parent or digest(copy) != asset["sha256"]:
            raise ValueError("Checkpoint asset integrity failure")
        original = Path(asset["original"])
        if not original.is_file() or digest(original) != asset["sha256"]:
            relink.append({"original": str(original), "backup": str(copy)})
    out = fresh(output_path, ".flp")
    copy_new(project, out)
    return {"restored": str(out), "sha256": digest(out), "needs_relink": relink,
            "missing_at_checkpoint": manifest["missing_samples"]}


@mcp.tool()
def restore_portable_checkpoint(manifest_path: str, output_path: str) -> dict:
    """Restore to a NEW FLP and sibling media folder, relinking captured sample events.

    Hash-checks every backup before writing. Original song/media are never replaced.
    Opaque plugin state is preserved, but references hidden inside plugins, external
    libraries/licenses, missing-at-checkpoint files and unsaved edits are not restored.
    """
    manifest_file = source(manifest_path, ".json")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    project = source(manifest["project"], ".flp")
    if project.parent != manifest_file.parent or digest(project) != manifest["sha256"]:
        raise ValueError("Checkpoint project integrity failure")
    out = fresh(output_path, ".flp")
    media = out.with_suffix(".assets")
    if media.exists():
        raise ValueError("Portable media destination already exists")
    verified = []
    mapping = {}
    for i, asset in enumerate(manifest["assets"]):
        copy = source(asset["copy"])
        if copy.parent != manifest_file.parent or digest(copy) != asset["sha256"]:
            raise ValueError("Checkpoint asset integrity failure")
        target = media / f"asset-{i}{copy.suffix}"
        for reference in asset.get("references", []) + [asset["original"]]:
            mapping[reference] = str(target)
        verified.append((copy, target))
    raw = flp_bytes.relink_samples(project.read_bytes(), mapping)
    media.mkdir()
    for copy, target in verified:
        with target.open("xb") as stream, copy.open("rb") as original:
            shutil.copyfileobj(original, stream)
    with out.open("xb") as stream:
        stream.write(raw)
    return {"restored": str(out), "media": str(media), "sha256": digest(out),
            "assets_restored": len(verified), "missing_at_checkpoint": manifest["missing_samples"],
            "scope": "Captured sample events relinked; embedded plugin-library references not inspected"}


@mcp.tool()
def project_preflight(project_path: str) -> dict:
    """Inspect saved references/Playlist byte sizes before native opening or rendering.

    No connection to FL, playback, plugin loading or cloud request. Structural
    validity is not proof of native compatibility, audible routing or plugin licenses.
    """
    src = source(project_path, ".flp")
    raw = src.read_bytes()
    _, events = flp_bytes.parse(raw)
    missing = []
    for name in flp_bytes.sample_references(raw):
        p = Path(name)
        if not (p if p.is_absolute() else src.parent / p).is_file():
            missing.append(name)
    return {"project": str(src), "sha256": digest(src), "event_count": len(events),
            "playlist_payload_bytes": [len(p) for p in flp_bytes.payloads(raw, 233)],
            "missing_samples": missing, "native_acceptance": "unverified",
            "warnings": ["Unsaved edits and embedded plugin library paths are outside this inspection",
                         "Playlist format and plugin availability need native validation"]}


@mcp.tool()
def list_autosaves(limit: int = 20) -> dict:
    """List saved FL autosaves from this PC's configured global Backup directory.

    Does not enable autosave or recover RAM never saved. Newest completed files
    appear first. Use checkpoint_autosave before risky edits or restoring a song.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be 1..100")
    directory = Path(os.environ.get("FLSTUDIO_MCP_DATA_DIR", "C:/Image-Line/FL Studio")) / "Projects/Backup"
    files = sorted(directory.glob("*.flp"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"directory": str(directory), "autosaves": [
        {"path": str(p), "bytes": p.stat().st_size, "modified": p.stat().st_mtime}
        for p in files[:limit]], "autosave_enabled": "not established by files on disk"}


@mcp.tool()
def checkpoint_autosave(autosave_path: str) -> dict:
    """Capture a completed, parseable autosave as an immutable project checkpoint.

    Requires the configured global Backup directory, stable bytes and a file at
    least 3 seconds old. Does not force a save of the current in-memory session.
    """
    p = source(autosave_path, ".flp")
    directory = Path(os.environ.get("FLSTUDIO_MCP_DATA_DIR", "C:/Image-Line/FL Studio")) / "Projects/Backup"
    if p.parent != directory.resolve() or time.time() - p.stat().st_mtime < 3:
        raise ValueError("Use a completed autosave in the configured Backup folder")
    before = p.read_bytes()
    flp_bytes.parse(before)
    result = checkpoint_project(str(p))
    if digest(result["project"]) != hashlib.sha256(before).hexdigest() or p.read_bytes() != before:
        raise ValueError("Autosave changed during capture; retry after it finishes")
    return result


@mcp.tool()
def checkpoint_library(library_path: str, maximum_bytes: int = 2147483648,
                       maximum_files: int = 10000) -> dict:
    """Version an explicit external sample/preset library with hashes and relative paths.

    Local files only; symlinks/junctions/cloud placeholders are refused. Limits
    are checked before copying. No plugin activation/license or whole-PC backup.
    Reuse restore_library for a verified NEW directory. Large collections can use
    a separately configured deduplicated backup system instead.
    """
    root = Path(library_path)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError("Use an existing absolute ordinary library directory")
    root = root.resolve()
    if not 1 <= maximum_bytes <= 100 * 1024**3 or not 1 <= maximum_files <= 100000:
        raise ValueError("Invalid explicit library limits")
    files = []
    size = 0
    for parent, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            p = Path(parent) / name
            attrs = getattr(p.stat(follow_symlinks=False), "st_file_attributes", 0)
            if p.is_symlink() or p.is_junction() or attrs & (0x400 | 0x1000 | 0x40000):
                raise ValueError("Library links/reparse/cloud files are not captured: " + str(p))
        for name in names:
            p = Path(parent) / name
            size += p.stat().st_size
            files.append(p)
            if size > maximum_bytes or len(files) > maximum_files:
                raise ValueError("Library exceeds supplied file/byte limits; nothing copied")
    folder = JOBS / ("library-" + uuid.uuid4().hex)
    media = folder / "files"
    media.mkdir(parents=True)
    records = []
    for p in files:
        relative = p.relative_to(root)
        out = media / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        before = digest(p)
        copy_new(p, out)
        if digest(out) != before or digest(p) != before:
            raise ValueError("Library changed during capture: " + str(p))
        records.append({"relative": relative.as_posix(), "sha256": before, "bytes": out.stat().st_size})
    manifest = {"kind": "external-library", "original": str(root), "files": records,
                "file_count": len(records), "bytes": size,
                "scope": "Explicit disk assets; does not back up activation or unsaved plugin memory"}
    write_json(folder / "manifest.json", manifest)
    return {"manifest": str(folder / "manifest.json"), **manifest}


@mcp.tool()
def restore_library(manifest_path: str, output_directory: str) -> dict:
    """Verify and restore a captured library into a NEW directory, preserving paths.

    Does not change vendor paths or activate plugins. Relink the instrument through
    its supported library settings and verify playback before claiming recall.
    """
    manifest_file = source(manifest_path, ".json")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("kind") != "external-library":
        raise ValueError("Not a library checkpoint")
    out = Path(output_directory)
    if not out.is_absolute() or out.exists():
        raise ValueError("Use a new absolute destination directory")
    verified = []
    base = (manifest_file.parent / "files").resolve()
    for row in manifest["files"]:
        relative = Path(row["relative"])
        if relative.is_absolute() or ".." in relative.parts or ":" in str(relative):
            raise ValueError("Invalid library relative path")
        backup = source(str(base / relative))
        if not backup.is_relative_to(base) or digest(backup) != row["sha256"]:
            raise ValueError("Library checkpoint integrity failure")
        verified.append((backup, relative))
    out.mkdir(parents=True)
    for backup, relative in verified:
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_new(backup, destination)
    return {"restored": str(out), "files": len(verified), "bytes": manifest["bytes"], "activation_restored": False}


@mcp.tool()
def export_note_midi(output_path: str, notes: list[dict], tempo: float = 120) -> dict:
    """Create standard format-0 MIDI without FL or per-session script arming.

    Notes use pitch(0..127), start/duration in quarter-note beats, velocity(1..127)
    and optional channel(0..15). Import into FL later; no live piano-roll write occurs.
    """
    if not math.isfinite(tempo) or not 10 <= tempo <= 999 or not 1 <= len(notes) <= 10000:
        raise ValueError("Invalid tempo or note count (1..10000)")
    out = fresh(output_path, ".mid")
    timeline = []
    last_tick = 0
    for note in notes:
        pitch, velocity, channel = note["pitch"], note.get("velocity", 100), note.get("channel", 0)
        start, duration = float(note["start"]), float(note["duration"])
        if (any(type(n) is not int for n in (pitch, velocity, channel)) or not 0 <= pitch <= 127
                or not 1 <= velocity <= 127 or not 0 <= channel <= 15
                or not math.isfinite(start) or not math.isfinite(duration)
                or not 0 <= start <= 1000000 or not 0 < duration <= 1000000):
            raise ValueError("Invalid MIDI note")
        begin, end = round(start * 480), round((start + duration) * 480)
        if end <= begin or end > 0x0fffffff:
            raise ValueError("Note outside MIDI timing range")
        timeline.extend([(begin, 1, bytes([0x90 | channel, pitch, velocity])),
                         (end, 0, bytes([0x80 | channel, pitch, 0]))])
        last_tick = max(last_tick, end)
    def vlq(value):
        chunks = [value & 127]
        while value >> 7:
            value >>= 7
            chunks.append((value & 127) | 128)
        return bytes(reversed(chunks))
    track = bytearray(b"\0\xff\x51\x03" + round(60000000 / tempo).to_bytes(3, "big"))
    previous = 0
    for tick, _, message in sorted(timeline):
        track.extend(vlq(tick - previous) + message)
        previous = tick
    track.extend(b"\0\xff\x2f\0")
    with out.open("xb") as stream:
        stream.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
                     + b"MTrk" + struct.pack(">I", len(track)) + track)
    return {"midi": str(out), "sha256": digest(out), "note_count": len(notes),
            "duration_seconds": last_tick / 480 * 60 / tempo, "live_notes_written": False}


@mcp.tool()
def render_saved_project(project_path: str, timeout_seconds: int = 180,
                         minimum_duration_seconds: float = 0) -> dict:
    """Start bounded offline WAV render of a saved COPY using Image-Line's CLI.

    Returns job ID immediately. Check render_status; starting a job is not success.
    Uses a separate FL process; may consume CPU or encounter plugin dialogs.
    """
    if not 10 <= timeout_seconds <= 600:
        raise ValueError("Timeout must be 10..600 seconds")
    if not math.isfinite(minimum_duration_seconds) or minimum_duration_seconds < 0:
        raise ValueError("Minimum duration must be finite and nonnegative")
    src = source(project_path, ".flp")
    if not FL.is_file():
        raise ValueError("Configured FL executable is absent")
    running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10)
    if running.returncode:
        raise ValueError("Could not establish whether an interactive FL session is open")
    if '"fl64.exe"' in running.stdout.lower():
        raise ValueError("Close/save the interactive FL session before offline rendering; concurrent-instance behavior is not verified")
    JOBS.mkdir(parents=True, exist_ok=True)
    for existing in JOBS.glob("render-*/job.json"):
        previous = json.loads(existing.read_text(encoding="utf-8"))
        if previous["state"] in ("queued", "running") and time.time() - previous["created"] < previous["timeout"] + 30:
            raise ValueError("A render is already active; check its status first")
    job_id = "render-" + uuid.uuid4().hex
    lock = JOBS / "render.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            json.dump({"job_id": job_id, "created": time.time()}, stream)
    except FileExistsError:
        raise ValueError("Another renderer owns the lock; inspect status before starting another job")
    folder = JOBS / job_id
    folder.mkdir()
    raw = src.read_bytes()
    references = {}
    for reference in flp_bytes.sample_references(raw):
        relative = Path(reference)
        if not relative.is_absolute() and (src.parent / relative).is_file():
            references[reference] = str((src.parent / relative).resolve())
    (folder / "input.flp").write_bytes(flp_bytes.relink_samples(raw, references))
    job = {"job_id": job_id, "state": "queued", "created": time.time(),
           "timeout": timeout_seconds, "source_sha256": hashlib.sha256(raw).hexdigest(),
           "executable": str(FL),
           "minimum_duration_seconds": minimum_duration_seconds}
    write_json(folder / "job.json", job)
    try:
        with (folder / "worker.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__)), "--render-job", str(folder)],
                                       stdout=log, stderr=log, creationflags=0x08000000)
        job["worker_pid"] = process.pid
    except Exception:
        lock.unlink(missing_ok=True)
        job["state"] = "failed"
        job["error"] = "Worker failed to start"
        write_json(folder / "job.json", job)
        raise
    return job


@mcp.tool()
def render_status(job_id: str) -> dict:
    """Read durable job status. completed requires FFmpeg decoding verification."""
    if not job_id.startswith("render-") or len(job_id) != 39 or not all(c in "0123456789abcdef" for c in job_id[7:]):
        raise ValueError("Invalid job ID")
    return json.loads((JOBS / job_id / "job.json").read_text(encoding="utf-8"))


@mcp.tool()
def measure_audio(audio_path: str) -> dict:
    """Measure full saved mix offline: EBU R128 loudness/true peak, without playback.

    Use companion audio_analyzer.compare for spectral/stereo A/B; report uncertain
    tempo as half/double candidates. Never claim listening proves exact notes/lyrics.
    """
    p = source(audio_path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("FFmpeg unavailable")
    result = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-threads", "1", "-filter_threads", "1", "-i", str(p),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(f"Audio decode/measurement failed (FFmpeg exit {result.returncode})")
    start = result.stderr.rfind("{")
    if start < 0:
        raise ValueError("FFmpeg returned no loudness report")
    metrics, _ = json.JSONDecoder().raw_decode(result.stderr[start:])
    return {"sha256": digest(p), "measurements": {k: v for k, v in metrics.items() if k.startswith("input_")},
            "scope": "Whole audio file; no changes applied"}


def audio_metadata(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("FFprobe unavailable")
    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Audio metadata inspection failed")
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise ValueError("File contains no audio stream")
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Audio has no finite positive duration")
    return {"duration_seconds": duration, "sample_rate": int(data["streams"][0]["sample_rate"]),
            "channels": data["streams"][0]["channels"]}


@mcp.tool()
def validate_audio_export(audio_path: str, minimum_duration_seconds: float) -> dict:
    """Decode and reject silence/short output before accepting an exported mix.

    Requires a finite nonnegative expected minimum. Passing proves only these checks,
    not that the file came from a particular song or that its content is musically correct.
    """
    if not math.isfinite(minimum_duration_seconds) or minimum_duration_seconds < 0:
        raise ValueError("Invalid minimum duration")
    path = source(audio_path)
    metadata = audio_metadata(path)
    qa = measure_audio(str(path))
    problems = []
    peak = float(qa["measurements"]["input_tp"])
    if not math.isfinite(peak):
        problems.append("Audio is silent or has no finite measured peak")
    if metadata["duration_seconds"] + 0.05 < minimum_duration_seconds:
        problems.append("Audio is shorter than the required minimum")
    return {**qa, **metadata, "accepted": not problems, "problems": problems,
            "minimum_duration_seconds": minimum_duration_seconds,
            "scope": "Decoding, duration and non-silence only; native project/content identity unverified"}


@mcp.tool()
def compare_mix_exports(before_path: str, after_path: str) -> dict:
    """Measure both complete exports offline; report after-minus-before LUFS/peak deltas.

    No normalization, playback or cloud upload. Different length/channel/rate is
    flagged. These measurements cannot certify preservation of words/notes or quality.
    """
    before, after = source(before_path), source(after_path)
    a, b = measure_audio(str(before)), measure_audio(str(after))
    am, bm = audio_metadata(before), audio_metadata(after)
    deltas = {}
    for name in ("input_i", "input_tp", "input_lra"):
        x, y = float(a["measurements"][name]), float(b["measurements"][name])
        deltas[name] = round(y - x, 3) if math.isfinite(x) and math.isfinite(y) else None
    warnings = []
    if abs(am["duration_seconds"] - bm["duration_seconds"]) > 0.05:
        warnings.append("Different durations: full-file measurements cover different material")
    for field in ("sample_rate", "channels"):
        if am[field] != bm[field]:
            warnings.append(f"Different {field}")
    return {"before": {**a, **am}, "after": {**b, **bm}, "after_minus_before": deltas,
            "warnings": warnings, "perceptual_quality": "not established by measurements"}


@mcp.tool()
def prepare_listening_excerpt(audio_path: str, output_path: str, start_seconds: float = 0,
                              duration_seconds: float = 30) -> dict:
    """Make a NEW bounded 24kHz stereo PCM16 WAV for optional cloud listening later.

    Local only, up to60s (~5.8MB). Removes metadata; does not contact Google.
    Original remains unchanged. Excerpt resampling is for listening, not full-band QA.
    """
    if (not math.isfinite(start_seconds) or not math.isfinite(duration_seconds)
            or start_seconds < 0 or not 1 <= duration_seconds <= 60):
        raise ValueError("Use nonnegative start and duration1..60 seconds")
    src = source(audio_path)
    metadata = audio_metadata(src)
    if start_seconds >= metadata["duration_seconds"]:
        raise ValueError("Excerpt starts after the audio ends")
    out = fresh(output_path, ".wav")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("FFmpeg unavailable")
    result = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-n", "-ss", str(start_seconds),
        "-i", str(src), "-t", str(duration_seconds), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
        "-ac", "2", "-ar", "24000", "-c:a", "pcm_s16le", str(out)],
        capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError("Excerpt export failed")
    return {"excerpt": str(out), "sha256": digest(out), "source_sha256": digest(src),
            "start_seconds": start_seconds, **audio_metadata(out), "bytes": out.stat().st_size,
            "cloud_uploaded": False}


@mcp.tool()
def save_parameter_map(plugin_name: str, plugin_version: str, parameter_index: int,
                       parameter_name: str, observations: list[dict]) -> dict:
    """Save supplied live-read observations {normalized,display} for one plugin/version.

    Supply actual fl_plugin_get_params v/s readbacks. No live reads/writes occur here;
    observations are caller-supplied, not independently certified calibration. Each map
    gets a new ID, never replaces another version. Do not invent Hz/dB conversions.
    """
    if (not all(isinstance(s, str) and 0 < len(s.strip()) <= 200
                for s in (plugin_name, plugin_version, parameter_name))
            or type(parameter_index) is not int or not 0 <= parameter_index <= 100000
            or not 1 <= len(observations) <= 256):
        raise ValueError("Supply plugin/version/parameter identity and1..256 observations")
    points = []
    for item in observations:
        value = item.get("normalized")
        display = item.get("display")
        if (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                or not isinstance(display, str) or not 1 <= len(display) <= 200):
            raise ValueError("Each observation needs normalized0..1 and its actual display string")
        points.append({"normalized": value, "display": display})
    profile_id = "parameter-" + uuid.uuid4().hex
    folder = ROOT / "runtime/parameter-maps"
    folder.mkdir(parents=True, exist_ok=True)
    record = {"profile_id": profile_id, "plugin_name": plugin_name, "plugin_version": plugin_version,
              "parameter_index": parameter_index, "parameter_name": parameter_name,
              "observations": points, "evidence": "caller-supplied FL readbacks; not independently verified"}
    write_json(folder / (profile_id + ".json"), record)
    return record


@mcp.tool()
def lookup_parameter_setting(profile_id: str, plugin_name: str, plugin_version: str,
                             display_value: str) -> dict:
    """Return an EXACT observed setting for matching plugin/version; never interpolates.

    Result is a proposal only. Verify plugin/parameter identity live, checkpoint, set
    through the existing safe live tool, read back display and restore on mismatch.
    """
    if (not profile_id.startswith("parameter-") or len(profile_id) != 42
            or any(c not in "0123456789abcdef" for c in profile_id[10:])):
        raise ValueError("Invalid parameter profile ID")
    record = json.loads((ROOT / "runtime/parameter-maps" / (profile_id + ".json")).read_text(encoding="utf-8"))
    if record["plugin_name"] != plugin_name or record["plugin_version"] != plugin_version:
        raise ValueError("Plugin identity/version does not match the recorded observations")
    matches = {p["normalized"] for p in record["observations"] if p["display"] == display_value}
    if len(matches) != 1:
        raise ValueError("Display value absent or maps to multiple normalized values; obtain live readback")
    return {"parameter_index": record["parameter_index"], "parameter_name": record["parameter_name"],
            "normalized": matches.pop(), "display": display_value, "applied": False,
            "evidence": record["evidence"], "requires_live_identity_and_readback_check": True}


def render_worker(folder):
    folder = Path(folder)
    job = json.loads((folder / "job.json").read_text())
    job["state"] = "running"
    write_json(folder / "job.json", job)
    try:
        running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10)
        if running.returncode or '"fl64.exe"' in running.stdout.lower():
            raise RuntimeError("FL opened before worker launch; refusing to disturb that session")
        result = subprocess.run([job.get("executable", str(FL)), "/Ewav", "/O" + str(folder), "/R", str(folder / "input.flp")],
                                timeout=job["timeout"], capture_output=True, creationflags=0x08000000)
        output = folder / "input.wav"
        if result.returncode or not output.is_file():
            raise RuntimeError("FL did not produce a WAV; inspect FL/plugin dialogs")
        qa = validate_audio_export(str(output), job.get("minimum_duration_seconds", 0))
        if not qa["accepted"]:
            raise RuntimeError("Render validation failed: " + "; ".join(qa["problems"]))
        job.update(state="completed", output=str(output), qa=qa)
    except Exception as error:
        job.update(state="failed", error=f"{type(error).__name__}: {error}")
    write_json(folder / "job.json", job)
    lock = JOBS / "render.lock"
    if lock.is_file() and json.loads(lock.read_text())["job_id"] == job["job_id"]:
        lock.unlink()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--render-job":
        render_worker(sys.argv[2])
    else:
        mcp.run()
