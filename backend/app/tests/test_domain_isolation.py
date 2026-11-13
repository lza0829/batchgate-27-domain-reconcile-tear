"""Domain isolation: same-domain runs, cross-domain is blocked, sums reconcile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from app.services.orchestrate_service import run_orchestration


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # app.config imported DATA_DIR at import time; patch the active modules too.
    import app.config as app_config
    import app.db as app_db

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_db, "DB_PATH", tmp_path / "app.db")
    from app.seed import init_db

    init_db()
    with TestClient(app) as c:
        yield c


def _jobs(client: TestClient) -> dict[str, dict]:
    return {j["name"]: j for j in client.get("/api/jobs").json()}


def test_seed_has_domains(client: TestClient):
    jobs = _jobs(client)
    assert jobs["ingest_orders"]["domain"] == "sales"
    assert jobs["fact_order_daily"]["domain"] == "sales"
    assert jobs["customer_profile"]["domain"] == "customer"
    assert jobs["marketing_attribution"]["domain"] == "marketing"


def test_same_domain_dependencies_run(client: TestClient):
    run = client.post("/api/orchestrations").json()
    attempts = client.get(
        f"/api/attempts?orchestration_id={run['id']}"
    ).json()
    by_job = {a["job_name"]: a for a in attempts}

    assert by_job["ingest_orders"]["status"] == "success"
    assert by_job["fact_order_daily"]["status"] == "success"
    assert by_job["customer_profile"]["status"] == "success"
    assert by_job["ingest_orders"]["domain"] == "sales"
    assert by_job["fact_order_daily"]["domain"] == "sales"

    sales = next(d for d in run["report"]["domains"] if d["domain"] == "sales")
    assert sales["succeeded"] == 2
    assert sales["layer_count"] == 2


def test_cross_domain_dependency_is_blocked(client: TestClient):
    run = client.post("/api/orchestrations").json()
    attempts = client.get(
        f"/api/attempts?orchestration_id={run['id']}"
    ).json()
    by_job = {a["job_name"]: a for a in attempts}

    blocked = by_job["marketing_attribution"]
    assert blocked["status"] == "blocked_domain"
    assert blocked["domain"] == "marketing"
    assert "cross-domain" in blocked["detail"]["reason"]
    assert blocked["detail"]["blocked_edges"] == [
        {
            "job": "marketing_attribution",
            "job_domain": "marketing",
            "depends_on": "ingest_orders",
            "dep_domain": "sales",
        }
    ]
    # No successful execution of the cross-domain job.
    assert not any(
        a["job_name"] == "marketing_attribution" and a["status"] == "success"
        for a in client.get("/api/attempts").json()
    )
    assert run["report"]["blocked_domain"] == 1
    assert run["report"]["blocked_edges"] == [
        {
            "job": "marketing_attribution",
            "job_domain": "marketing",
            "depends_on": "ingest_orders",
            "dep_domain": "sales",
        }
    ]
    # Upstream success in another domain gives no permission to run.
    assert by_job["fact_order_daily"]["status"] == "success"


def test_per_domain_sums_equal_global(client: TestClient):
    run = client.post("/api/orchestrations").json()
    report = run["report"]
    domains = report["domains"]
    for key in (
        "succeeded",
        "failed",
        "skipped",
        "skipped_dep",
        "blocked_domain",
        "job_total",
        "attempt_count",
        "watermarked_jobs",
    ):
        assert sum(d[key] for d in domains) == report[key], key
    assert report["domain_consistent"] is True

    stats = client.get("/api/dashboard").json()
    for key in (
        "job_count",
        "enabled_jobs",
        "attempt_count",
        "succeeded",
        "failed",
        "skipped",
        "blocked_domain",
        "watermarked_jobs",
    ):
        assert sum(d[key] for d in stats["domains"]) == stats[key], key
    assert stats["domain_consistent"] is True


def test_domain_change_rejected_when_edge_crosses(client: TestClient):
    jobs = _jobs(client)
    resp = client.patch(
        f"/api/jobs/{jobs['fact_order_daily']['id']}/domain",
        json={"domain": "finance"},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "cross_domain_dependency"
    assert any(c["depends_on"] == "ingest_orders" for c in detail["conflicts"])

    # Unchanged after the rejected save.
    after = _jobs(client)["fact_order_daily"]
    assert after["domain"] == "sales"


def test_domain_change_allowed_for_isolated_job(client: TestClient):
    jobs = _jobs(client)
    resp = client.patch(
        f"/api/jobs/{jobs['customer_profile']['id']}/domain",
        json={"domain": "customer_profile"},
    )
    assert resp.status_code == 200
    assert resp.json()["domain"] == "customer_profile"
    assert "customer_profile" in client.get("/api/domains").json()["domains"]


def test_domain_change_rejected_when_downstream_stays(client: TestClient):
    jobs = _jobs(client)
    # Moving the upstream while fact_order_daily stays in sales would break ordering.
    resp = client.patch(
        f"/api/jobs/{jobs['ingest_orders']['id']}/domain",
        json={"domain": "raw"},
    )
    assert resp.status_code == 409
    assert _jobs(client)["ingest_orders"]["domain"] == "sales"


def test_attempt_rows_carry_domain(client: TestClient):
    client.post("/api/orchestrations")
    for a in client.get("/api/attempts").json():
        assert a["domain"] in {"sales", "customer", "marketing"}


def test_report_is_persisted_with_domain_slices(client: TestClient):
    run = client.post("/api/orchestrations").json()
    again = client.get(f"/api/orchestrations/{run['id']}").json()
    assert again["report"]["domains"] == run["report"]["domains"]
    assert again["report"]["domain_consistent"] is True


def test_unknown_dependency_is_skipped_not_run(client: TestClient):
    conn = connect()
    conn.execute(
        "UPDATE jobs SET depends_on_json=? WHERE name='customer_profile'",
        ('["does_not_exist"]',),
    )
    conn.commit()
    conn.close()

    run = client.post("/api/orchestrations").json()
    attempts = {
        a["job_name"]: a
        for a in client.get(f"/api/attempts?orchestration_id={run['id']}").json()
    }
    assert attempts["customer_profile"]["status"] == "skipped_dep"
    assert "does_not_exist" in attempts["customer_profile"]["detail"]["reason"]
    assert attempts["fact_order_daily"]["status"] == "success"


def test_legacy_database_is_migrated(tmp_path: Path, monkeypatch):
    """A pre-domain app.db keeps its graph, defaulting to one domain."""
    import sqlite3

    import app.config as app_config
    import app.db as app_db
    from app.seed import init_db

    legacy_path = tmp_path / "app.db"
    conn = sqlite3.connect(legacy_path)
    conn.executescript(
        """
        CREATE TABLE jobs(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            fixture_path TEXT NOT NULL, depends_on_json TEXT NOT NULL,
            max_attempts INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
            note TEXT
        );
        CREATE TABLE job_attempts(
            id INTEGER PRIMARY KEY, orchestration_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL, attempt_no INTEGER NOT NULL,
            status TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO jobs(name, fixture_path, depends_on_json)
        VALUES ('ingest_orders','ingest_orders.json','[]');
        INSERT INTO job_attempts(orchestration_id, job_id, attempt_no, status, detail, created_at)
        VALUES (1,1,1,'success','{}','2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_db, "DB_PATH", legacy_path)
    init_db()

    conn = app_db.connect()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "domain" in cols
    job = dict(conn.execute("SELECT name, domain FROM jobs").fetchone())
    assert job["domain"] == "sales"
    attempt = dict(conn.execute("SELECT job_id, domain FROM job_attempts").fetchone())
    assert attempt["domain"] == "sales"
    conn.close()
