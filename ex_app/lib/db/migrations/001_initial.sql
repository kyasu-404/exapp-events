CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE source_files (
 file_id TEXT PRIMARY KEY, path TEXT NOT NULL, etag TEXT, mtime TEXT,
 content_hash TEXT, last_seen_at TEXT, last_sync_at TEXT, status TEXT, last_error TEXT
);
CREATE TABLE source_events (
 source_key TEXT PRIMARY KEY, file_id TEXT NOT NULL, sheet TEXT NOT NULL,
 event_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
 location TEXT NOT NULL, title TEXT NOT NULL, responsible TEXT NOT NULL,
 data_hash TEXT NOT NULL, internal_uid TEXT NOT NULL, public_uid TEXT NOT NULL,
 public_enabled INTEGER NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE INDEX source_events_file ON source_events(file_id);
CREATE TABLE calendar_events (
 target TEXT NOT NULL, uid TEXT NOT NULL, source_key TEXT, href TEXT,
 etag TEXT, data_hash TEXT, status TEXT NOT NULL, last_error TEXT,
 PRIMARY KEY(target, uid)
);
CREATE TABLE reminders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_uid TEXT NOT NULL, recipient TEXT NOT NULL,
 offset_seconds INTEGER NOT NULL, scheduled_at TEXT NOT NULL, status TEXT NOT NULL,
 attempt_count INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, last_error TEXT,
 event_revision INTEGER NOT NULL, dedupe_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sent_at TEXT
);
CREATE INDEX reminder_due ON reminders(status, next_attempt_at, scheduled_at);
CREATE TABLE event_queue (file_id TEXT PRIMARY KEY, due_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE sync_runs (
 id TEXT PRIMARY KEY, type TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, files_scanned INTEGER DEFAULT 0, events_created INTEGER DEFAULT 0,
 events_updated INTEGER DEFAULT 0, events_deleted INTEGER DEFAULT 0,
 warnings INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, result TEXT
);
CREATE TABLE logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, level TEXT NOT NULL,
 subsystem TEXT NOT NULL, file TEXT, sheet TEXT, operation TEXT, result TEXT NOT NULL
);
CREATE INDEX logs_filter ON logs(at, level, subsystem);
