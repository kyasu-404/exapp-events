import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "events.sqlite3"
        self.lock = threading.RLock()
        self.transaction_depth = 0
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self):
        with self.lock:
            exists = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE name='schema_migrations'"
            ).fetchone()
            applied = (
                {r[0] for r in self.conn.execute("SELECT version FROM schema_migrations")}
                if exists
                else set()
            )
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                version = int(path.name.split("_")[0])
                if version not in applied:
                    self.conn.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + path.read_text(encoding="utf-8")
                        + f"\nINSERT INTO schema_migrations VALUES({version},'{now()}'); COMMIT;"
                    )

    def execute(self, sql: str, params=()):
        with self.lock:
            try:
                cursor = self.conn.execute(sql, params)
                if not self.transaction_depth:
                    self.conn.commit()
                return cursor
            except Exception:
                if not self.transaction_depth:
                    self.conn.rollback()
                raise

    @contextmanager
    def transaction(self):
        with self.lock:
            outer = self.transaction_depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self.transaction_depth += 1
            try:
                yield
                if outer:
                    self.conn.commit()
            except BaseException:
                if outer:
                    self.conn.rollback()
                raise
            finally:
                self.transaction_depth -= 1

    def rows(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params)]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def meta(self, key: str, default="") -> str:
        r = self.one("SELECT value FROM metadata WHERE key=?", (key,))
        return r["value"] if r else default

    def set_meta(self, key: str, value: str):
        self.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, value))

    def log(self, level: str, subsystem: str, result: str, file="", sheet="", operation=""):
        # Only controlled messages are accepted by callers; never raw network exceptions or credentials.
        self.execute(
            "INSERT INTO logs(at,level,subsystem,file,sheet,operation,result) VALUES(?,?,?,?,?,?,?)",
            (now(), level, subsystem, file, sheet, operation, result),
        )

    def record_sync_issues(self, issues):
        """Log an issue when it appears, rather than on every unchanged reconciliation."""
        previous = {dumps(issue) for issue in json.loads(self.meta("sync_issues", "[]"))}
        current = {dumps(issue): issue for issue in issues}
        with self.transaction():
            for key, issue in current.items():
                if key not in previous:
                    self.log(
                        issue["level"],
                        issue.get("subsystem", "Sync"),
                        issue["message"],
                        issue.get("file", ""),
                        issue.get("sheet", ""),
                        f"Строка {issue['row']}" if issue.get("row") else "",
                    )
            self.set_meta("sync_issues", dumps(list(current.values())))

    def recover(self):
        # SMTP acceptance and a SQLite commit cannot form a transaction. Do not blindly resend ambiguous jobs.
        self.execute(
            "UPDATE reminders SET status='failed',last_error='Результат SMTP неизвестен после остановки; проверьте доставку перед повтором' WHERE status='sending'"
        )
        self.execute(
            "UPDATE sync_runs SET status='interrupted',finished_at=? WHERE status IN ('queued','running')",
            (now(),),
        )

    def close(self):
        self.conn.close()
