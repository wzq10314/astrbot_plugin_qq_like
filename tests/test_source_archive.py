"""Verify installation exports cannot package account or downloaded data."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


spec = importlib.util.spec_from_file_location("source_archive", Path(__file__).parents[1] / "scripts" / "build_zip.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceArchiveTests(unittest.TestCase):
    def test_source_and_static_resources_survive_but_private_files_do_not(self):
        keep = {"main.py", "_conf_schema.json", "metadata.yaml", "LICENSE", "NOTICE.md",
                "licenses/pica-LICENSE.txt", "reader/server.py", "reader/web/app.js",
                "assets/menus/help.webp", "pixiv_reborn/data/helpmsg.json",
                "pixiv_reborn/data/SmileySans-Oblique.ttf", ".github/workflows/test.yml", ".gitignore"}
        private = {"reader/config.json", "reader-client.json", "config.yaml", "pica/token.json",
                   "pica/tokens/user.json", "state/collection/manifest.json", "state/collection/page.png",
                   "requests/request/page.webp", "downloads/book/page.jpg", "packs/book.zip",
                   "cache/original.png", "runtime/session.json", "pixiv_reborn/data/users.json",
                   "plugin_data/pica/token.json", "fanbox/downloaded.json", ".env", ".env.local",
                   "subscriptions.db", "subscriptions.db-wal", "reader/config.json.before-change",
                   "unknown/page.png", "__pycache__/main.pyc", ".git/config",
                   "account.json", "subscription.yaml", "option.yml", "credentials.toml"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in keep | private:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"export-test-fixture")
            self.assertEqual({path.relative_to(root).as_posix() for path in module.source_files(root)}, keep)
            archive = module.build_archive(root, root / "install.zip")
            with zipfile.ZipFile(archive) as handle:
                self.assertEqual(set(handle.namelist()), {"astrbot_plugin_qq_like/" + name for name in keep})

    def test_symlink_escape_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            root.mkdir()
            outside = Path(directory) / "private.json"
            outside.write_bytes(b"export-test-fixture")
            try:
                (root / "escaped.json").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not available")
            self.assertEqual(list(module.source_files(root)), [])
