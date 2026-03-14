import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
sys.path.insert(0, str(API_DIR))


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


class SmokeApiTests(unittest.TestCase):
    def test_sync_roundtrip_used_by_connect(self):
        sync = load_module(API_DIR / "sync.py", "sync_mod")
        with tempfile.TemporaryDirectory() as td:
            sync.SYNC_DIR = td
            sync._cache = {}
            token = "ML-TESTSYNC01"
            payload = {
                "name": "Test Project",
                "instr": "instr",
                "files": [{"name": "a.txt"}],
                "chunks": [{"text": "hello"}],
                "hist": [{"role": "user", "content": "hi"}],
            }
            sync._save(token, payload)
            got = sync._load(token)
            self.assertIsInstance(got, dict)
            self.assertEqual(got["name"], "Test Project")
            self.assertEqual(got["instr"], "instr")

    def test_codex_queue_flow_used_by_codex_command(self):
        codex = load_module(API_DIR / "codex.py", "codex_mod")
        with tempfile.TemporaryDirectory() as td:
            codex.QUEUE_DIR = td
            codex._cache = {}
            token = "ML-TESTCODEX01"
            task = codex._enqueue(token, {"task": "fix bug", "chat_id": 1, "user_id": 2})
            self.assertEqual(task["status"], "queued")
            picked = codex._claim(token, {"worker_id": "w1", "stale_sec": 10})
            self.assertEqual(picked["id"], task["id"])
            done = codex._complete(token, {"id": task["id"], "status": "done", "result": "ok"})
            self.assertEqual(done["status"], "done")

    def test_upload_and_rag_search_smoke(self):
        storage = load_module(API_DIR / "_storage.py", "storage_mod")
        with tempfile.TemporaryDirectory() as td:
            storage.STORE_DIR = td
            storage.META_FILE = os.path.join(td, "meta.json")
            uploaded = storage.upload_files([("test.txt", "Договор и поправки закона".encode("utf-8"))])
            self.assertEqual(len(uploaded), 1)
            stats = storage.get_stats()
            self.assertEqual(stats["total_files"], 1)
            found = storage.search("поправки закона")
            self.assertGreaterEqual(found["chunks_found"], 1)

    def test_search_domain_cleaning(self):
        search = load_module(API_DIR / "search.py", "search_mod")
        domains = search._clean_domains([
            "https://www.consultant.ru/path",
            "garant.ru",
            "garant.ru",
            "not-a-domain",
            "https://pravo.gov.ru",
        ])
        self.assertIn("consultant.ru", domains)
        self.assertIn("garant.ru", domains)
        self.assertIn("pravo.gov.ru", domains)
        self.assertEqual(len(domains), len(set(domains)))


if __name__ == "__main__":
    unittest.main()
