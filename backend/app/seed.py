from app.db import connect


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> bool:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    return True


def _migrate(conn) -> None:
    """Add domain columns to databases created before domain isolation."""
    added_job_domain = _add_column_if_missing(
        conn, "jobs", "domain", "domain TEXT NOT NULL DEFAULT 'sales'"
    )
    added_attempt_domain = _add_column_if_missing(
        conn, "job_attempts", "domain", "domain TEXT NOT NULL DEFAULT ''"
    )
    if added_attempt_domain:
        # Backfill historical attempts from the job they belong to.
        conn.execute(
            """
            UPDATE job_attempts
            SET domain = COALESCE((SELECT j.domain FROM jobs j WHERE j.id = job_attempts.job_id), '')
            WHERE domain = ''
            """
        )
    if added_job_domain:
        # Legacy installs keep their existing (pre-domain) graph intact by
        conn.execute("UPDATE jobs SET domain='sales' WHERE domain='' OR domain IS NULL")


def init_db():
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS jobs(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            domain TEXT NOT NULL DEFAULT 'sales',
            fixture_path TEXT NOT NULL,
            depends_on_json TEXT NOT NULL,
            max_attempts INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS orchestrations(
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            report_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_attempts(
            id INTEGER PRIMARY KEY,
            orchestration_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            domain TEXT NOT NULL DEFAULT '',
            attempt_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watermarks(
            job_id INTEGER PRIMARY KEY,
            watermark_ts TEXT,
            artifact_rows INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );
        """
    )
    _migrate(conn)
    if conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0:
        jobs = [
            (
                "ingest_orders",
                "sales",
                "ingest_orders.json",
                "[]",
                1,
                1,
                "销售域入口：无依赖，写出订单水位",
            ),
            (
                "fact_order_daily",
                "sales",
                "fact_order_daily.json",
                '["ingest_orders"]',
                1,
                1,
                "销售域内依赖：依赖同域 ingest_orders 成功后执行",
            ),
            (
                "customer_profile",
                "customer",
                "dim_customer.json",
                "[]",
                1,
                1,
                "客户域：无依赖，写出客户维表水位",
            ),
            (
                "marketing_attribution",
                "marketing",
                "marketing_attribution.json",
                '["ingest_orders"]',
                1,
                1,
                "演示用跨域边：marketing 域依赖 sales 域 ingest_orders，编排时会被域隔离拦下",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO jobs(name, domain, fixture_path, depends_on_json, max_attempts, enabled, note)
            VALUES (?,?,?,?,?,?,?)
            """,
            jobs,
        )
        for row in conn.execute("SELECT id FROM jobs").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO watermarks(job_id, watermark_ts, artifact_rows, updated_at) VALUES (?,?,?,?)",
                (row["id"], None, 0, None),
            )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            ("default_max_attempts", "1"),
        )
    conn.commit()
    conn.close()
