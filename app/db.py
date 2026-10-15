"""Tiny DB layer: sqlite for tests/local, Postgres on Render. SQL is written with '?' placeholders."""
import json, time, uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, shop TEXT, status TEXT NOT NULL, filename TEXT, file BYTEA,
  pregnant TEXT, weeks INTEGER, patient TEXT, rows TEXT, lab_notes TEXT, text TEXT, error TEXT,
  created_at DOUBLE PRECISION NOT NULL, picked_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS shop_counts (shop TEXT PRIMARY KEY, n INTEGER NOT NULL);
"""
JSON_COLS = ("patient", "rows", "lab_notes")


class Db:
    def __init__(self, url):
        if url.startswith("sqlite:///"):
            import sqlite3
            self.conn = sqlite3.connect(url[len("sqlite:///"):], check_same_thread=False, isolation_level=None)
            self.ph = "?"
            for stmt in SCHEMA.replace("BYTEA", "BLOB").replace("DOUBLE PRECISION", "REAL").split(";"):
                if stmt.strip(): self.conn.execute(stmt)
        else:
            import psycopg
            self.conn = psycopg.connect(url, autocommit=True)
            self.ph = "%s"
            for stmt in SCHEMA.split(";"):
                if stmt.strip(): self.conn.execute(stmt)

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql.replace("?", self.ph), params)
        return cur

    def one(self, sql, params=()):
        cur = self.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(zip([c[0] for c in cur.description], row))
        for k in JSON_COLS:
            if d.get(k):
                d[k] = json.loads(d[k])
        return d

    def create_job(self, shop, filename, data, pregnant, weeks):
        jid = uuid.uuid4().hex
        self.execute("INSERT INTO jobs (id, shop, status, filename, file, pregnant, weeks, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (jid, shop, "queued", filename, data, pregnant, weeks, time.time()))
        if shop:
            self.execute("INSERT INTO shop_counts (shop, n) VALUES (?, 1) ON CONFLICT (shop) DO UPDATE SET n = shop_counts.n + 1", (shop,))
        return jid

    def get(self, jid):
        return self.one("SELECT * FROM jobs WHERE id = ?", (jid,))

    def take_next(self):
        """Oldest queued/confirmed job; claimed with a status check so two workers can't take the same job."""
        for _ in range(3):
            j = self.one("SELECT * FROM jobs WHERE status IN ('queued','confirmed') ORDER BY created_at LIMIT 1")
            if j is None:
                return None
            new = "reading" if j["status"] == "queued" else "explaining"
            cur = self.execute("UPDATE jobs SET status = ?, file = NULL, picked_at = ? WHERE id = ? AND status = ?",
                               (new, time.time(), j["id"], j["status"]))
            if cur.rowcount == 1:
                return j
        return None

    def update(self, jid, **cols):
        for k in JSON_COLS:
            if k in cols:
                cols[k] = json.dumps(cols[k], ensure_ascii=False)
        sets = ", ".join(f"{k} = ?" for k in cols)
        return self.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*cols.values(), jid)).rowcount

    def delete_older_than(self, seconds):
        return self.execute("DELETE FROM jobs WHERE created_at < ?", (time.time() - seconds,)).rowcount

    def shop_count(self, shop):
        r = self.one("SELECT n FROM shop_counts WHERE shop = ?", (shop,))
        return r["n"] if r else 0


def connect(url):
    return Db(url)
