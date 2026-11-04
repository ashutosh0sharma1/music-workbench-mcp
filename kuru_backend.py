"""Bounded subprocess adapter to the pinned file backend, with live access blocked."""
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "runtime/kuru-file-backend"
PIN = "d4b9567c65fe1e2e95ab13b0b5cdac78d769be05"


def call(action, payload):
    message = json.dumps({"action": action, **payload}, allow_nan=False)
    if len(message.encode()) > 4 * 1024 * 1024:
        raise ValueError("Composition request exceeds 4 MiB")
    result = subprocess.run([sys.executable, str(Path(__file__)), "--worker"], input=message,
                            capture_output=True, text=True, timeout=90,
                            creationflags=0x08000000 if sys.platform == "win32" else 0)
    if result.returncode:
        raise ValueError("File backend failed: " + result.stderr[-1500:])
    answer = json.loads(result.stdout)
    if not answer.get("ok"):
        raise ValueError(answer.get("error", "File backend failed"))
    return answer["result"]


def block_live(name, args):
    if name in {"subprocess.Popen", "os.system", "socket.connect", "socket.bind", "ctypes.dlopen",
                "winreg.CreateKey", "winreg.SetValue", "winreg.DeleteKey", "winreg.DeleteValue"}:
        raise RuntimeError(f"File backend prohibits live operation: {name}")


def validate_doc(doc):
    if not isinstance(doc, dict):
        raise ValueError("Composition must be an object")
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite composition value")
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)
    finite(doc)
    project = doc.get("project", {})
    if not 10 <= project.get("bpm", 120) <= 999:
        raise ValueError("Tempo must be 10..999")
    # Compact reads omit unmapped events and unplaced patterns. Only full export
    # is accepted when modifying an existing file, to avoid silently losing them.
    if doc.get("format") == "compact" and (doc.get("base") or project.get("source")):
        raise ValueError("Existing projects require a full composition export, not compact view")
    if sum(len(p.get("notes", [])) for p in doc.get("patterns", [])) > 100000:
        raise ValueError("At most 100000 notes per request")
    ids = [c["id"] for key in ("instruments", "samples", "automation") for c in doc.get(key, [])]
    if len(set(ids)) != len(ids):
        raise ValueError("Channel IDs must be unique")


def worker(request):
    manifest = json.loads((BACKEND / "manifest.json").read_text(encoding="utf-8"))
    if manifest["pin"] != PIN:
        raise ValueError("Unexpected file backend pin")
    for rel, expected in manifest["files"].items():
        if hashlib.sha256((BACKEND / rel).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Backend integrity failure: {rel}")
    sys.addaudithook(block_live)
    sys.path.insert(0, str(BACKEND / "scripts"))
    import config
    config.FILE = ROOT / "runtime/kuru-file-settings.json"
    # Override detection of the user's nondefault data folder. Never harvest a
    # plugin live, change MIDI settings or open a preset in FL from this process.
    import library
    library.harvester = None
    action = request["action"]
    if action == "plugins":
        rows = library.summary(request.get("query"), native=True)
        return {"plugins": rows[:request.get("limit", 100)], "total": len(rows), "loaded_or_licensed": "not tested"}
    if action in {"presets", "params"}:
        entry = library.find(request["plugin"])
        rows = library.presets(entry, request.get("query")) if action == "presets" else library.params(entry, request.get("query"))
        return {"plugin": entry["name"], "format": entry["format"], action: rows[:request.get("limit", 100)],
                "total": len(rows), "evidence": "catalogue only; installed-version native acceptance required"}
    import project
    if action == "read":
        return project.export(request["project_path"])
    if action != "write":
        raise ValueError("Unsupported backend action")
    doc = request["composition"]
    validate_doc(doc)
    base = request.get("base_project") or doc.get("project", {}).get("source") or doc.get("base")
    if base and doc.get("format") == "compact":
        raise ValueError("Existing projects require the full composition export")
    original_hash = None
    if base:
        base = Path(base)
        if not base.is_absolute() or not base.is_file() or base.suffix.lower() != ".flp":
            raise ValueError("Existing base must be an absolute FLP path")
        original_hash = hashlib.sha256(base.read_bytes()).hexdigest()
        # Unmapped global and mixer state must come from the actual original.
        doc["base"] = str(base)
        doc.setdefault("mixer", {})["from"] = str(base)
    import writer
    raw, warnings = writer.build(doc)
    if warnings:
        raise ValueError("Unmapped changes refused: " + "; ".join(warnings))
    if base and hashlib.sha256(base.read_bytes()).hexdigest() != original_hash:
        raise ValueError("Base changed during generation; retry from a fresh snapshot")
    out = Path(request["output_path"])
    if not out.is_absolute() or out.suffix.lower() != ".flp":
        raise ValueError("Output must be a new absolute FLP path")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("xb") as stream:
        stream.write(raw)
    exported = project.export(str(out))
    return {"project": str(out), "sha256": hashlib.sha256(raw).hexdigest(), "base_sha256": original_hash,
            "warnings": warnings, "readback": exported, "native_validated": False}


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read(4 * 1024 * 1024 + 1))
        with contextlib.redirect_stdout(io.StringIO()):
            answer = worker(request)
        print(json.dumps({"ok": True, "result": answer}, allow_nan=False))
    except Exception as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
