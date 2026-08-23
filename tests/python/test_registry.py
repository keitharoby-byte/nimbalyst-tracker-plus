from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reader.contracts import ReaderError
from reader.registry import _load_bundle_from, effective_registry

ROOT = Path(__file__).resolve().parents[2]


class RegistryTests(unittest.TestCase):
    @staticmethod
    def _bundle_fixture(directory: Path) -> dict[str, object]:
        files: dict[str, str] = {}
        for name in ("registry.py", "registry.json", "saved-queries.json"):
            target = directory / name
            shutil.copyfile(ROOT / "reader" / name, target)
            files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest: dict[str, object] = {
            "formatVersion": 1,
            "generationId": hashlib.sha256(
                json.dumps(files, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "extensionVersion": "9.9.9",
            "adapterVersion": 4,
            "registryVersion": 5,
            "files": files,
        }
        (directory / "bundle-manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        return manifest

    def test_verified_bundle_reports_release_and_asset_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._bundle_fixture(root)
            registry, diagnostics = _load_bundle_from(root, require_manifest=True)
            self.assertEqual(registry["version"], 5)
            self.assertEqual(registry["savedQueries"], {})
            self.assertEqual(diagnostics["verificationState"], "verified")
            self.assertEqual(diagnostics["extensionVersion"], "9.9.9")
            self.assertEqual(diagnostics["adapterVersion"], 4)
            self.assertEqual(diagnostics["generationId"], manifest["generationId"])
            self.assertEqual(
                set(diagnostics["assetHashes"]),
                {"registry.py", "registry.json", "saved-queries.json"},
            )

    def test_mismatched_bundle_fails_with_actionable_restart_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._bundle_fixture(root)
            (root / "saved-queries.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ReaderError) as raised:
                _load_bundle_from(root, require_manifest=True)
            error = raised.exception
            self.assertEqual(error.code, "READER_RESTART_REQUIRED")
            self.assertEqual(error.details["extensionVersion"], "9.9.9")
            self.assertEqual(error.details["adapterVersion"], 4)
            self.assertEqual(error.details["registryVersion"], 5)
            self.assertTrue(error.details["assetPath"].endswith("saved-queries.json"))
            self.assertEqual(len(error.details["expectedHash"]), 64)
            self.assertEqual(len(error.details["actualHash"]), 64)

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

    def test_invalid_dispatch_evidence_override_is_ignored_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nimbalyst").mkdir()
            (root / ".nimbalyst" / "tracker-plus.registry.json").write_text(json.dumps({
                "dispatchEvidence": {
                    "qaStatus": {
                        "sources": [{
                            "kind": "tag",
                            "tag": "qa-signed-off",
                            "value": True,
                        }],
                    },
                },
            }), encoding="utf-8")

            registry, active, error, _registry_hash = effective_registry(root)

            self.assertFalse(active)
            self.assertIsNotNone(error)
            self.assertEqual(
                registry["dispatchEvidence"]["qaStatus"]["sources"],
                [{"kind": "field", "field": "qaStatus"}],
            )

    def test_missing_query_catalog_has_no_saved_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry, active, error, _registry_hash = effective_registry(directory)

            self.assertFalse(active)
            self.assertIsNone(error)
            self.assertEqual(registry["savedQueries"], {})

    def test_external_query_catalog_is_the_complete_query_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nimbalyst").mkdir()
            catalog = {
                "version": 1,
                "queries": {
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
            self.assertEqual(set(registry["savedQueries"]), {"workspace-ready-items"})

    def test_null_query_entries_are_invalid_in_an_authoritative_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_directory = root / ".nimbalyst"
            config_directory.mkdir()
            (config_directory / "tracker-plus.queries.json").write_text(
                json.dumps({"version": 1, "queries": {"legacy-query": None}}),
                encoding="utf-8",
            )

            registry, active, error, _registry_hash = effective_registry(root)

            self.assertFalse(active)
            self.assertIsNotNone(error)
            self.assertEqual(registry["savedQueries"], {})

    def test_legacy_registry_saved_queries_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_directory = root / ".nimbalyst"
            config_directory.mkdir()
            (config_directory / "tracker-plus.registry.json").write_text(
                json.dumps({
                    "savedQueries": {
                        "legacy-query": {
                            "version": 1,
                            "kind": "predicate",
                            "params": [],
                            "definition": {
                                "where": {"field": "status", "op": "eq", "value": "ready"},
                            },
                        },
                    },
                }),
                encoding="utf-8",
            )

            registry, active, error, _registry_hash = effective_registry(root)

            self.assertFalse(active)
            self.assertIsNotNone(error)
            self.assertEqual(registry["savedQueries"], {})

    def test_published_external_query_catalog_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_directory = root / ".nimbalyst"
            config_directory.mkdir()
            shutil.copyfile(
                ROOT / "examples" / "tracker-plus.queries.json",
                config_directory / "tracker-plus.queries.json",
            )

            registry, active, error, _registry_hash = effective_registry(root)

            self.assertTrue(active)
            self.assertIsNone(error)
            published = json.loads(
                (ROOT / "examples" / "tracker-plus.queries.json").read_text(
                    encoding="utf-8",
                )
            )["queries"]
            self.assertEqual(set(registry["savedQueries"]), set(published))
            self.assertNotIn("walk-ready-milestones", registry["savedQueries"])
            self.assertEqual(
                {
                    query["kind"]
                    for query in registry["savedQueries"].values()
                },
                {"predicate", "traversal"},
            )


if __name__ == "__main__":
    unittest.main()
