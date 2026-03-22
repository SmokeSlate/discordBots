import gc
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


class SQLiteStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir.name)
        self.db_path = Path(self.temp_dir.name) / "test_data.sqlite3"
        os.environ["SMOKEBOT_DB_PATH"] = str(self.db_path)

        existing_module = sys.modules.get("SmokeBot.storage")
        if existing_module is not None:
            if hasattr(existing_module, "close_storage"):
                existing_module.close_storage()
            self.storage = importlib.reload(existing_module)
        else:
            self.storage = importlib.import_module("SmokeBot.storage")

    def tearDown(self):
        self.storage.close_storage()
        del self.storage
        gc.collect()
        os.chdir(self.original_cwd)
        self.temp_dir.cleanup()
        os.environ.pop("SMOKEBOT_DB_PATH", None)

    def test_round_trip_single_file_sqlite(self):
        payload = {"guild": "123", "items": ["a", "b"]}
        self.storage.write_json("snippets.json", payload)

        loaded = self.storage.read_json("snippets.json", {})

        self.assertEqual(payload, loaded)
        self.assertTrue(self.db_path.exists())

    def test_migrates_existing_json_file_into_sqlite(self):
        legacy_file = Path("legacy_test.json")
        try:
            legacy_file.write_text('{"migrated": true}', encoding="utf-8")
            loaded = self.storage.read_json(str(legacy_file), {})
            self.assertEqual({"migrated": True}, loaded)

            # ensure data now comes from sqlite as well
            legacy_file.unlink()
            loaded_again = self.storage.read_json(str(legacy_file), {})
            self.assertEqual({"migrated": True}, loaded_again)
        finally:
            if legacy_file.exists():
                legacy_file.unlink()

    def test_bulk_migration_imports_known_files_once(self):
        Path("snippets.json").write_text('{"legacy": true}', encoding="utf-8")

        summary = self.storage.migrate_legacy_json_files(["snippets.json"])
        self.assertEqual(["snippets.json"], summary["migrated"])
        self.assertEqual({}, summary["errors"])
        self.assertEqual({"legacy": True}, self.storage.read_json("snippets.json", {}))

        second_summary = self.storage.migrate_legacy_json_files(["snippets.json"])
        self.assertEqual([], second_summary["migrated"])
        self.assertEqual({}, second_summary["errors"])


if __name__ == "__main__":
    unittest.main()
