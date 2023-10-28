import sqlite3
from supr import log, IDLE_TIMEOUT


class localstate:
    """
    State tracks instance activity and runtime.
    """
    _schema = """
    CREATE TABLE IF NOT EXISTS instance_runtime (
        native_id text, 
        start datetime, 
        stop datetime
    );
    CREATE INDEX IF NOT EXISTS idx_native_id ON instance_runtime(native_id);
    CREATE TABLE IF NOT EXISTS instance_activity (
        native_id text primary key, 
        last_interacted datetime
    );"""
    @classmethod
    def activity_callback(cls, native_id): # thread-safe
        from supr import DB_PATH
        return lambda: cls(DB_PATH).activity(native_id)
    def __init__(self, filename):
        self.db = sqlite3.connect(filename, isolation_level=None)
        self.db.executescript(self._schema)       
    def activity(self, native_id):
        log.debug(f"activity {native_id}")
        self.db.execute("""
        INSERT INTO instance_activity (native_id, last_interacted) 
        VALUES (?, current_timestamp)
        ON CONFLICT (native_id) DO UPDATE SET last_interacted=current_timestamp
        """, (native_id, ))
    def get_all(self):
        return self.conn.execute("""
        SELECT native_id, last_interacted
        FROM instance_activity 
        """).fetchall()
    def get_idle(self):
        return self.db.execute("""
        SELECT native_id
        FROM instance_activity 
        WHERE last_interacted < datetime('now', '-%s seconds')
        """ % IDLE_TIMEOUT.total_seconds()).fetchall()
    def get_runtime(self):
        return self.db.execute("""
        SELECT native_id, sum(julianday(coalesce(stop, current_timestamp)) - julianday(start)) * 24 * 60 * 60 as runtime
        FROM instance_runtime
        GROUP BY native_id
        """).fetchall()
    def change(self, native_id, state):
        self.activity(native_id)
        if state == 'start':
            self.db.execute("""
            INSERT INTO instance_runtime (native_id, start) 
            VALUES (?, current_timestamp)
            """, (native_id, ))
        elif state in ('stop', 'terminate'):
            self.db.execute("""
            UPDATE instance_runtime 
            SET stop=current_timestamp 
            WHERE native_id=? AND stop IS NULL
            """, (native_id, ))

