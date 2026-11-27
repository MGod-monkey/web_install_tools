import json
import sqlite3
from datetime import datetime, timezone
from .config import state_dir


class History:
    def __init__(self):
        self.path = state_dir() / "history.sqlite3"
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, host TEXT NOT NULL,
                username TEXT NOT NULL, port INTEGER NOT NULL, service TEXT NOT NULL,
                package TEXT NOT NULL, version TEXT NOT NULL, state TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '')""")
        self.path.chmod(0o600)

    def _connection(self):
        return sqlite3.connect(self.path)

    def create(self, task_id, robot, package):
        with self._connection() as db:
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    datetime.now(timezone.utc).isoformat(),
                    robot.host,
                    robot.username,
                    robot.port,
                    robot.service,
                    package.path.name,
                    "",
                    "准备中",
                    "",
                ),
            )

    def update(self, task_id, state, detail=""):
        with self._connection() as db:
            db.execute("UPDATE tasks SET state=?, detail=? WHERE id=?", (state, detail, task_id))

    def set_package(self, task_id, name, version):
        with self._connection() as db:
            db.execute("UPDATE tasks SET package=?, version=? WHERE id=?", (name, version, task_id))

    def recent(self):
        with self._connection() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT 100")]

    def export(self, path):
        path.write_text(json.dumps(self.recent(), ensure_ascii=False, indent=2), encoding="utf-8")
