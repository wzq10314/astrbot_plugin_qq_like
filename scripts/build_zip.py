"""Build a portable AstrBot installation ZIP from the plugin source tree."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
output = root.parent / "astrbot_plugin_qq_like-v1.6.1.zip"
excluded = {"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules", "_test_data", "state", "packs", "downloads"}
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink():
            continue
        if excluded.intersection(relative.parts) or path.suffix in {".pyc", ".pyo", ".zip", ".log", ".db", ".sqlite", ".sqlite3"}:
            continue
        if path.name in {".env", "reader-client.json"} or relative.as_posix() == "reader/config.json" or ".before-" in path.name:
            continue
        archive.write(path, (Path("astrbot_plugin_qq_like") / relative).as_posix())
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
    assert all("\\" not in name for name in archive.namelist())
print(output)
