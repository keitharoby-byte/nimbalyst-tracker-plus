"""Build deterministic Tracker+ scale fixtures (items plus 1.5x links)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def make_fixture(output: Path, item_count: int, workspace: str) -> None:
    if item_count < 10:
        raise ValueError("item_count must be at least 10")
    if output.exists():
        output.unlink()
    connection = sqlite3.connect(output)
    try:
        connection.executescript((ROOT / "fixtures/sql/tracker-schema-current.sql").read_text(encoding="utf-8"))
        rows: list[tuple[object, ...]] = []
        launch_data = {
            "title": "Scale launch", "launchKey": "SCALE-1", "status": "active",
            "owner": "coordinator", "audience": ["internal"], "scopeRevision": "1",
            "entryCriteria": [{}], "exitCriteria": [{}],
        }
        rows.append(_row("scale-launch", "LAUNCH-SCALE-1", "launch", launch_data, workspace, 0))
        for index in range(1, item_count):
            data = {
                "title": f"Scale work {index}",
                "status": "in-progress" if index % 7 else "done",
                "owner": "coordinator" if index % 11 == 0 else f"owner-{index % 17}",
                "priority": ("low", "medium", "high", "critical")[index % 4],
                "tags": ["needs-coordination"] if index % 29 == 0 else [],
                "dueDate": f"2026-{8 + (index % 4):02d}-{1 + (index % 27):02d}",
            }
            rows.append(_row(f"item-{index:06d}", f"SCALE-{index:06d}", "task", data, workspace, index))
        link_count = round(item_count * 1.5)
        for index in range(link_count):
            if index < min(item_count - 1, 120):
                source = f"item-{index + 1:06d}"
                target = "scale-launch"
                relationship_type = "part-of-launch"
                extra = {"scopeRole": "core" if index < 20 else "supporting"}
            else:
                normalized = index - min(item_count - 1, 120)
                source_index = 1 + normalized % (item_count - 1)
                pass_index = normalized // (item_count - 1)
                target_index = 1 + (source_index + 7 + pass_index * 13) % (item_count - 1)
                source = f"item-{source_index:06d}"
                target = f"item-{target_index:06d}"
                relationship_type = "depends-on" if index % 3 == 0 else "coordinates-with"
                extra = {"hardness": "hard-serial"} if relationship_type == "depends-on" else {}
            data = {
                "title": f"Scale link {index}", "sourceItem": {"itemId": source},
                "targetItem": {"itemId": target}, "relationshipType": relationship_type,
                "status": "active", **extra,
            }
            rows.append(_row(f"link-{index:06d}", f"SCALE-LINK-{index:06d}", "timeline-link", data, workspace, item_count + index))
        connection.executemany(
            """INSERT INTO tracker_items (id, issue_number, issue_key, type, data, workspace, content, archived, type_tags, deleted_at, created, updated, last_indexed)
               VALUES (?, ?, ?, ?, ?, ?, '', 0, ?, NULL, '2026-07-16T00:00:00Z', ?, '2026-07-16T00:00:00Z')""",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _row(item_id: str, issue_key: str, item_type: str, data: dict[str, object], workspace: str, number: int) -> tuple[object, ...]:
    return (item_id, number + 1, issue_key, item_type, json.dumps(data, separators=(",", ":")), workspace, json.dumps([item_type]), f"2026-07-16T00:{number % 60:02d}:{number % 60:02d}Z")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, required=True, choices=[1500, 5000, 20000])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    make_fixture(args.output, args.items, args.workspace or str(args.output.parent.resolve()))
    print(args.output)
