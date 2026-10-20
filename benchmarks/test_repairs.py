"""Regressions for fact identity, store ownership and MCP fault containment.

Run with python3 benchmarks/test_repairs.py. Standard library only.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from myelinate import MyelinatedMemory
from myelinated_mcp import McpServer


class RepairTests(unittest.TestCase):
    def test_distinct_text_survives_refresh_and_persistence(self):
        pairs = [
            ("Alice paid Bob.", "Bob paid Alice."),
            ("Deploy is allowed.", "Deploy is not allowed."),
            ("Use API_KEY.", "Use api_key."),
            ("Price is 1.50.", "Price is 150."),
            ("Use very little salt.", "Use very very little salt."),
            ("用户喜欢简洁回复", "用户喜欢详细回复"),
            ("The deploy target for the web service in the eu-west region is the staging cluster "
             "owned by the platform team for nightly builds",
             "The deploy target for the web service in the eu-west region is the production cluster "
             "owned by the platform team for nightly builds"),
        ]
        for a, b in pairs:
            with self.subTest(a=a, b=b), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "memory.json")
                with MyelinatedMemory(path=path) as engine:
                    first = engine.add(a)
                    second = engine.add(b)
                    self.assertNotEqual(first, second)
                    engine.refresh()
                    engine.save()
                with MyelinatedMemory(path=path) as restored:
                    self.assertEqual({m.content for m in restored.all() if not m.retired}, {a, b})

    def test_exact_duplicate_and_explicit_replacement(self):
        with MyelinatedMemory(in_memory=True) as engine:
            first = engine.add("Alice paid Bob.")
            self.assertEqual(first, engine.add("  Alice paid Bob. \n"))
            second = engine.add("Bob paid Alice.", supersedes=first)
            self.assertTrue(engine.get(first).retired)
            self.assertEqual(engine.get(first).superseded_by, second)
            self.assertNotIn(first, engine.recall(query="paid").used_ids)

    def test_invalid_replacements_leave_state_unchanged(self):
        with MyelinatedMemory(in_memory=True) as engine:
            first = engine.add("Alice paid Bob.")
            for content, options in [
                ("New fact", {"supersedes": "missing"}),
                ("Alice paid Bob.", {"supersedes": first}),
                ("New fact", {"memory_id": first, "supersedes": first}),
                ("New fact", {"memory_id": first}),
            ]:
                before = copy.deepcopy(engine.to_dict())
                with self.assertRaises(ValueError):
                    engine.add(content, **options)
                self.assertEqual(engine.to_dict(), before)

    def test_reversed_relationship_is_not_a_duplicate(self):
        engine = MyelinatedMemory(in_memory=True)
        a = engine.add("Alice paid Bob.")
        b = engine.add("Bob paid Alice.")
        self.assertNotEqual(a, b)
        engine.refresh()
        self.assertEqual({m.content for m in engine.all() if not m.retired},
                         {"Alice paid Bob.", "Bob paid Alice."})

    def test_second_writer_cannot_overwrite_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.json")
            first = MyelinatedMemory(path=path)
            try:
                first.add("First writer fact.")
                first.save()
                with self.assertRaises(RuntimeError):
                    MyelinatedMemory(path=path)
            finally:
                if hasattr(first, "close"):
                    first.close()

    def test_malformed_params_do_not_crash(self):
        server = McpServer(MyelinatedMemory(in_memory=True))
        response = server.handle_line(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]}))
        self.assertEqual(json.loads(response)["error"]["code"], -32602)
        response = server.handle_line('{"jsonrpc":"2.0","id":2,"method":"ping"}')
        self.assertEqual(json.loads(response)["result"], {})

    def test_owner_exit_and_crash_release_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.json")
            source = ("from myelinate import MyelinatedMemory; import sys; "
                      "m=MyelinatedMemory(path=sys.argv[1]); "
                      "m.add('Saved before crash.'); m.save(); "
                      "print('ready',flush=True); sys.stdin.readline()")
            env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"))
            owner = subprocess.Popen([sys.executable, "-u", "-c", source, path],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env=env)
            try:
                self.assertEqual(owner.stdout.readline().strip(), "ready")
                contender = subprocess.run([sys.executable, str(ROOT / "scripts/myelinate.py"),
                    "--store", path, "add", "--content", "Must not overwrite."],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(contender.returncode, 2)
                self.assertIn("store busy", contender.stderr)
                self.assertNotIn("Traceback", contender.stderr)
                mcp = subprocess.run([sys.executable, str(ROOT / "scripts/myelinated_mcp.py"),
                    "--store", path], input="", capture_output=True, text=True, timeout=10)
                self.assertEqual(mcp.returncode, 2)
                self.assertEqual(mcp.stdout, "")
                self.assertIn("store busy", mcp.stderr)
            finally:
                owner.kill()
                owner.communicate(timeout=10)
            with MyelinatedMemory(path=path) as restored:
                self.assertEqual([m.content for m in restored.all()], ["Saved before crash."])
            # The previous context exited cleanly, so ownership is available again.
            with MyelinatedMemory(path=path) as successor:
                self.assertEqual(successor.size(), 1)

    def test_store_aliases_contend_and_other_stores_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            with MyelinatedMemory(path=str(path)) as owner:
                owner.save()
                with self.assertRaises(RuntimeError):
                    MyelinatedMemory(path=str(path.parent / "." / path.name))
                alias = Path(tmp) / "alias.json"
                try:
                    alias.symlink_to(path)
                except OSError:
                    pass  # Windows CI may not grant symlink creation privileges.
                else:
                    with self.assertRaises(RuntimeError):
                        MyelinatedMemory(path=str(alias))
                with MyelinatedMemory(path=str(Path(tmp) / "other.json")) as other:
                    other.add("Independent store.")
                    other.save()

    def test_closed_and_alternate_paths_cannot_publish_stale_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "memory.json")
            other = str(Path(tmp) / "other.json")
            with MyelinatedMemory(path=path) as owner:
                owner.add("Existing fact.")
                owner.save()
                with self.assertRaises(ValueError):
                    owner.save(other)
                with self.assertRaises(ValueError):
                    owner.load(other)
            with self.assertRaises(RuntimeError):
                owner.save()
            with MyelinatedMemory(in_memory=True) as snapshot:
                snapshot.add("Stale replacement.")
                with self.assertRaises(ValueError):
                    snapshot.save(path)
                self.assertEqual(snapshot.path, "")
                snapshot.save(other)
                self.assertEqual(snapshot.path, os.path.realpath(other))
            with MyelinatedMemory(path=path) as restored:
                self.assertEqual([m.content for m in restored.all()], ["Existing fact."])

    def test_failed_save_preserves_data_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            with MyelinatedMemory(path=str(path)) as owner:
                owner.add("Last saved fact.")
                owner.save()
                before = path.read_bytes()
                owner.add("Unsaved fact.")
                for name in ("myelinate.os.replace", "myelinate.json.dump"):
                    with self.subTest(failure=name), patch(name, side_effect=OSError("injected")):
                        with self.assertRaises(OSError):
                            owner.save()
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
            with MyelinatedMemory(path=str(path)) as restored:
                self.assertEqual(restored.size(), 1)

    def test_constructor_failure_releases_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            path.write_text("{invalid", encoding="utf-8")
            for _ in range(2):
                with self.assertRaises(ValueError):
                    MyelinatedMemory(path=str(path))
            path.write_text('{"schema":3,"memories":[]}', encoding="utf-8")
            with MyelinatedMemory(path=str(path)) as owner:
                self.assertEqual(owner.size(), 0)

    def test_protocol_invalid_calls_leave_memory_unchanged(self):
        server = McpServer(MyelinatedMemory(in_memory=True))
        server.engine.add("Original fact.")
        for params in [None, [], [1], "bad", True, {},
                {"name": []}, {"name": "unknown"},
                {"name": "memory_add", "arguments": None},
                {"name": "memory_add", "arguments": []},
                {"name": "memory_add", "arguments": {"content": 42}},
                {"name": "memory_add", "arguments": {"content": " "}},
                {"name": "memory_add", "arguments": {"content": "new", "protected": "false"}},
                {"name": "memory_recall", "arguments": {"budget": True}},
                {"name": "memory_recall", "arguments": {"budget": -1}},
                {"name": "memory_recall", "arguments": {"query": []}},
                {"name": "memory_refresh", "arguments": {"max_entries": -1}},
                {"name": "memory_retire", "arguments": {"id": []}},
                {"name": "memory_pin", "arguments": {"id": ""}}]:
            with self.subTest(params=params):
                before = copy.deepcopy(server.engine.to_dict())
                response = server.handle_message({"jsonrpc": "2.0", "id": 1,
                    "method": "tools/call", "params": params})
                self.assertEqual(response["error"]["code"], -32602)
                self.assertEqual(server.engine.to_dict(), before)

    def test_stdio_survives_bad_lines_and_restarts_with_saved_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "memory.json")
            lines = ['{bad json', 'null', '[1]', '[' * 2000 + '0' + ']' * 2000,
                json.dumps({"jsonrpc": "1.0", "id": 1, "method": "ping"}),
                json.dumps({"jsonrpc": "2.0", "id": {}, "method": "ping"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": 42}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": [1]}),
                json.dumps({"jsonrpc": "2.0", "method": "unknown/notification"}),
                json.dumps({"jsonrpc": "2.0", "method": "tools/call", "params": {
                    "name": "memory_add", "arguments": {"content": "Notification must not add."}}}),
                json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                    "name": "memory_add", "arguments": {"content": "Saved across restart."}}})]
            # A valid ping after every hostile line verifies recovery in the same process.
            stream = "".join(line + '\n' + json.dumps({"jsonrpc": "2.0", "id": 100 + i,
                "method": "ping"}) + '\n' for i, line in enumerate(lines))
            command = [sys.executable, str(ROOT / "scripts/myelinated_mcp.py"), "--store", path]
            result = subprocess.run(command, input=stream, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            pings = [r for r in responses if type(r.get("id")) is int and r["id"] >= 100]
            self.assertEqual(len(pings), len(lines))
            self.assertTrue(all(r.get("result") == {} for r in pings))
            self.assertEqual(len(responses), len(lines) * 2 - 2)  # two notifications
            self.assertNotIn("Traceback", result.stderr)
            recall = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                "name": "memory_recall", "arguments": {"query": "restart"}}}
            restarted = subprocess.run(command, input=json.dumps(recall) + '\n',
                capture_output=True, text=True, timeout=15)
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            response = json.loads(restarted.stdout)
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertIn("Saved across restart.", payload["text"])
            self.assertNotIn("Notification must not add.", payload["text"])

    def test_legacy_arms_are_pinned_and_new_arm_matches_defaults(self):
        sys.path.insert(0, str(ROOT / "benchmarks"))
        from engines import build_arms
        arms = {a.name: a for a in build_arms()}
        m11 = arms["M11 myelinated +lexical ranking"]
        m14 = arms["M14 myelinated +exact identity"]
        self.assertFalse(m11.engine.exact_identity)
        self.assertTrue(m14.engine.exact_identity)
        for name in ("use_similarity", "use_knapsack", "stale_retirement", "reinforcement",
                     "use_auto_supersede", "prior_weight", "recall_pool"):
            self.assertEqual(getattr(m11, name), getattr(m14, name))


if __name__ == "__main__":
    unittest.main()
