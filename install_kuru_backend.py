"""Install pinned MIT sources for the file adapter; never run live/UI/setup helpers."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parent
PIN = "d4b9567c65fe1e2e95ab13b0b5cdac78d769be05"
DEST = ROOT / "runtime/kuru-file-backend"


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "FL-Workbench-setup"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    if DEST.exists():
        raise SystemExit("Backend already exists; preserve it and verify its manifest")
    tree = json.loads(download(f"https://api.github.com/repos/whatanaiceguy/kuruitsugizaki/git/trees/{PIN}?recursive=1"))
    if tree.get("truncated"):
        raise RuntimeError("Incomplete upstream tree")
    selected = [e for e in tree["tree"] if e["type"] == "blob" and (
        (e["path"].startswith("scripts/") and e["path"].endswith(".py"))
        or e["path"] in {"LICENSE", "projects/init/init.flp", "projects/init/fx.flp"}
        or (e["path"].startswith("docs/params/") and e["path"].endswith(".tsv")))]
    stage = DEST.with_name(DEST.name + "-staging")
    stage.mkdir(parents=True, exist_ok=False)
    manifest = {"repository": "whatanaiceguy/kuruitsugizaki", "pin": PIN, "license": "MIT", "files": {}}
    cache = ROOT / "runtime/research-20261021/kuruitsugizaki"
    for entry in selected:
        rel = entry["path"]
        cached = cache / rel
        raw = cached.read_bytes() if cached.is_file() else download(f"https://raw.githubusercontent.com/whatanaiceguy/kuruitsugizaki/{PIN}/{rel}")
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != entry["sha"]:
            raise RuntimeError(f"Pinned Git blob hash mismatch: {rel}")
        out = stage / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        manifest["files"][rel] = hashlib.sha256(raw).hexdigest()
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    stage.rename(DEST)
    settings = ROOT / "runtime/kuru-file-settings.json"
    if not settings.exists():
        settings.write_text(json.dumps({
            "template": str(DEST / "projects/init/init.flp"),
            "effect_template": str(DEST / "projects/init/fx.flp"),
            "plugin_db": "C:/Image-Line/FL Studio/Presets/Plugin database",
            "fl_presets": "C:/Program Files/Image-Line/FL Studio 2026/Data/Patches/Plugin presets",
            "fl_user_presets": "C:/Image-Line/FL Studio/Presets/Plugin presets",
            "library": None, "hide_plugins": [], "sample_folders": [], "projects_folder": []
        }, indent=2), encoding="utf-8")
    print(json.dumps({"installed": str(DEST), "pin": PIN, "verified_files": len(selected), "live_or_ui_code_executed": False}))


if __name__ == "__main__":
    main()
