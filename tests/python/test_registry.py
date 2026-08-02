from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reader.registry import effective_registry


class RegistryTests(unittest.TestCase):
    def test_valid_override_changes_terminal_status_and_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nimbalyst").mkdir()
            (root / ".nimbalyst" / "tracker-plus.registry.json").write_text(json.dumps({
                "terminalStatuses": ["custom-complete"],
                "roles": {"qa": {"ownerAliases": ["quality"], "attentionTags": []}},
            }), encoding="utf-8")
            registry, active, error, registry_hash = effective_registry(root)
            self.assertTrue(active)
            self.assertIsNone(error)
            self.assertEqual(registry["terminalStatuses"], ["custom-complete"])
            self.assertIn("qa", registry["roles"])
            self.assertEqual(len(registry_hash), 12)

    def test_locked_or_malformed_override_is_ignored(self) -> None:
        for payload in ({"caps": {"queryLimitMax": 1000}}, "{"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".nimbalyst").mkdir()
                path = root / ".nimbalyst" / "tracker-plus.registry.json"
                path.write_text(json.dumps(payload) if isinstance(payload, dict) else payload, encoding="utf-8")
                registry, active, error, _registry_hash = effective_registry(root)
                self.assertFalse(active)
                self.assertIsNotNone(error)
                self.assertEqual(registry["caps"]["queryLimitMax"], 200)

    def test_external_query_catalog_can_replace_add_and_remove_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nimbalyst").mkdir()
            catalog = {
                "version": 1,
                "queries": {
                    "launch-open-reviews": None,
                    "workspace-ready-items": {
                        "version": 1,
                        "kind": "predicate",
                        "params": [],
                        "label": "Workspace-defined ready items",
                        "definition": {
                            "where": {
                                "field": "status",
                                "op": "eq",
                                "value": "ready",
                            }
                        },
                    },
                },
            }
            (root / ".nimbalyst" / "tracker-plus.queries.json").write_text(
                json.dumps(catalog),
                encoding="utf-8",
            )
            registry, active, error, _registry_hash = effective_registry(root)
            self.assertTrue(active)
            self.assertIsNone(error)
            self.assertNotIn("launch-open-reviews", registry["savedQueries"])
            self.assertIn("workspace-ready-items", registry["savedQueries"])


if __name__ == "__main__":
    unittest.main()
