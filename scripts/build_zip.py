"""Build an AstrBot ZIP containing source and static assets, never user data."""
import argparse
from pathlib import Path
import re
import zipfile


EXCLUDED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules",
    "_test_data", ".test-runtime", ".release-verification", "state", "requests",
    "packs", "downloads", "cache", "runtime", "logs", "tokens", "tmp", ".npm",
    "published_images", "menu_images", "plugin_data",
}
SOURCE_SUFFIXES = {
    ".py", ".md", ".txt", ".js", ".mjs", ".cjs", ".ts", ".html", ".css", ".sh",
}
STATIC_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ttf", ".otf", ".woff", ".woff2"}
STATIC_PIXIV_DATA = {"pixiv_reborn/data/helpmsg.json", "pixiv_reborn/data/SmileySans-Oblique.ttf"}
STRUCTURED_SOURCE_FILES = {
    "_conf_schema.json", "natural_commands.json", "pixiv_reborn/data/helpmsg.json",
    "metadata.yaml", "reader/compose.yaml", ".github/workflows/test.yml",
}
PRIVATE_NAMES = {"reader-client.json", "token.json", "downloaded.json", "config.json", "config.yaml", "config.yml"}


def source_files(root):
    root = Path(root).resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        relative = path.relative_to(root)
        parts = {part.lower() for part in relative.parts}
        name = path.name.lower()
        if EXCLUDED_DIRS & parts or name in PRIVATE_NAMES:
            continue
        if name == ".env" or name.startswith(".env.") or ".before-" in name:
            continue
        if any(part.startswith(".") and part not in {".github", ".gitignore", ".gitattributes"} for part in relative.parts):
            continue
        if "data" in parts and relative.as_posix() not in STATIC_PIXIV_DATA:
            continue
        if "config" in parts:
            continue
        if path.name in {"LICENSE", "NOTICE", ".gitignore", ".gitattributes"}:
            yield path
        elif relative.as_posix() in STRUCTURED_SOURCE_FILES:
            yield path
        elif path.suffix.lower() in SOURCE_SUFFIXES:
            yield path
        elif path.suffix.lower() in STATIC_SUFFIXES and (
            relative.parts[0] == "assets" or relative.as_posix() in STATIC_PIXIV_DATA or relative.as_posix() == "logo.png"
        ):
            yield path


def build_archive(root, output):
    root = Path(root).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files(root):
            archive.write(path, (Path("astrbot_plugin_qq_like") / path.relative_to(root)).as_posix())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive verification failed")
        if any("\\" in name or ".." in Path(name).parts for name in archive.namelist()):
            raise ValueError("Invalid archive member path")
    return output


def main():
    root = Path(__file__).resolve().parents[1]
    metadata = (root / "metadata.yaml").read_text(encoding="utf-8-sig")
    match = re.search(r"^version:\s*(v?\d+\.\d+\.\d+)\s*$", metadata, re.MULTILINE)
    if match is None:
        raise ValueError("Missing release version")
    version = match[1] if match[1].startswith("v") else "v" + match[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root.parent / f"astrbot_plugin_qq_like-{version}.zip")
    args = parser.parse_args()
    print(build_archive(root, args.output))


if __name__ == "__main__":
    main()
