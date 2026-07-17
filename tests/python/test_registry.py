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


if __name__ == "__main__":
    unittest.main()
