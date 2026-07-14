CREATE TABLE tracker_items (
  id TEXT PRIMARY KEY,
  issue_key TEXT,
  type TEXT NOT NULL,
  workspace TEXT NOT NULL,
  content TEXT,
  archived INTEGER NOT NULL DEFAULT 0,
  type_tags TEXT NOT NULL DEFAULT '[]',
  deleted_at TEXT,
  created TEXT NOT NULL,
  updated TEXT NOT NULL
);
