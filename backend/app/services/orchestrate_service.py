from __future__ import annotations

from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.dependency import (
    domain_edge_summary,
    plan_by_domain,
    ready_by_upstream_success,
    same_domain_depends_on,
)
from app.engines.domain_guard import (
    allow_cross_domain_execution,
    cross_domain_edges,
    cross_domain_edges_for_job,
)
from app.engines.job_runner import run_fixture_job
from app.repositories import jobs as jobs_repo
from app.repositories import orchestrations as orch_repo
from app.repositories import watermarks as wm_repo

_DOMAIN_STAT_KEYS = (
    "job_count",
    "enabled_jobs",
    "attempt_count",
    "succeeded",
    "failed",
    "skipped",
    "blocked_domain",
    "watermarked_jobs",
)

# Every counter that must reconcile between the global view and the domain slices.
_REPORT_SUM_KEYS = (
    "job_total",
    "attempt_count",
    "succeeded",
    "failed",
    "skipped",
    "skipped_dep",
    "blocked_domain",
    "watermarked_jobs",
)


def _empty_domain_stat(domain: str) -> dict[str, Any]:
    return {
        "domain": domain,
        **{key: 0 for key in _DOMAIN_STAT_KEYS},
    }


def _bucket_name(domain: str | None) -> str:
    # Rows with no domain (legacy/unassigned) still land in one visible bucket,
    # so the slices never silently drop a row from the global counters.
    return domain or "unassigned"


def _global_dashboard_counts(conn) -> dict[str, int]:
    """Global counters recomputed straight from the base tables (no GROUP BY).

    The dashboard compares this independent result with the sum of the domain
    slices so a tear cannot hide behind a single aggregation query.
    """
    jobs_sql = (
        "SELECT COUNT(*) AS job_count, "
        "SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled_jobs FROM jobs"
    )
    attempts_sql = """
        SELECT COUNT(*) AS attempt_count,
               SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS succeeded,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN status IN ('skipped_dep','blocked_domain') THEN 1 ELSE 0 END) AS skipped,
               SUM(CASE WHEN status='blocked_domain' THEN 1 ELSE 0 END) AS blocked_domain
        FROM job_attempts
    """
    wm_sql = """
        SELECT COUNT(*) AS c
        FROM watermarks w JOIN jobs j ON j.id=w.job_id
        WHERE w.watermark_ts IS NOT NULL
    """

    counts = {key: 0 for key in _DOMAIN_STAT_KEYS}
    for r in conn.execute(jobs_sql).fetchall():
        counts["job_count"] += r["job_count"] or 0
        counts["enabled_jobs"] += r["enabled_jobs"] or 0
    for r in conn.execute(attempts_sql).fetchall():
        counts["attempt_count"] += r["attempt_count"] or 0
        counts["succeeded"] += r["succeeded"] or 0
        counts["failed"] += r["failed"] or 0
        counts["skipped"] += r["skipped"] or 0
        counts["blocked_domain"] += r["blocked_domain"] or 0
    for r in conn.execute(wm_sql).fetchall():
        counts["watermarked_jobs"] += r["c"] or 0
    return counts


def dashboard_stats() -> dict[str, Any]:
    """Global counters plus per-domain slices; the two always add up."""
    conn = connect()
    try:
        per_domain: dict[str, dict[str, Any]] = {}

        def bucket(domain: str | None) -> dict[str, Any]:
            name = _bucket_name(domain)
            if name not in per_domain:
                per_domain[name] = _empty_domain_stat(name)
            return per_domain[name]

        for r in conn.execute(
            """
            SELECT domain,
                   COUNT(*) AS job_count,
                   SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled_jobs
            FROM jobs GROUP BY domain
            """
        ).fetchall():
            d = bucket(r["domain"])
            d["job_count"] = r["job_count"] or 0
            d["enabled_jobs"] = r["enabled_jobs"] or 0

        for r in conn.execute(
            """
            SELECT domain,
                   COUNT(*) AS attempt_count,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS succeeded,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN status IN ('skipped_dep','blocked_domain') THEN 1 ELSE 0 END) AS skipped,
                   SUM(CASE WHEN status='blocked_domain' THEN 1 ELSE 0 END) AS blocked_domain
            FROM job_attempts GROUP BY domain
            """
        ).fetchall():
            d = bucket(r["domain"])
            d["attempt_count"] = r["attempt_count"] or 0
            d["succeeded"] = r["succeeded"] or 0
            d["failed"] = r["failed"] or 0
            d["skipped"] = r["skipped"] or 0
            d["blocked_domain"] = r["blocked_domain"] or 0

        for r in conn.execute(
            """
            SELECT j.domain AS domain, COUNT(*) AS c
            FROM watermarks w JOIN jobs j ON j.id=w.job_id
            WHERE w.watermark_ts IS NOT NULL
            GROUP BY j.domain
            """
        ).fetchall():
            bucket(r["domain"])["watermarked_jobs"] = r["c"] or 0

        slices = sorted(per_domain.values(), key=lambda d: d["domain"])
        slice_totals = {
            key: sum(d[key] for d in slices) for key in _DOMAIN_STAT_KEYS
        }
        # Independently recompute the global counters (no GROUP BY) and compare.
        global_totals = _global_dashboard_counts(conn)
        domain_consistent = slice_totals == global_totals
        return {
            **slice_totals,
            "orchestration_count": conn.execute(
                "SELECT COUNT(*) c FROM orchestrations"
            ).fetchone()["c"],
            "domains": slices,
            "domain_consistent": domain_consistent,
        }
    finally:
        conn.close()


def _report_counts(attempts: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "succeeded": 0,
        "failed": 0,
        "skipped_dep": 0,
        "blocked_domain": 0,
        "skipped": 0,
    }
    for a in attempts:
        if a["status"] == "success":
            counts["succeeded"] += 1
        elif a["status"] == "failed":
            counts["failed"] += 1
        elif a["status"] == "skipped_dep":
            counts["skipped_dep"] += 1
        elif a["status"] == "blocked_domain":
            counts["blocked_domain"] += 1
    counts["skipped"] = counts["skipped_dep"] + counts["blocked_domain"]
    return counts


def _blocked_detail(blocked_edges: list[dict[str, Any]]) -> dict[str, Any]:
    reason = "; ".join(
        f"{e['job']}({e['job_domain']}) -> {e['depends_on']}({e['dep_domain']})"
        for e in blocked_edges
    )
    return {
        "reason": f"cross-domain dependency blocked: {reason}",
        "blocked_edges": blocked_edges,
    }


def run_orchestration() -> dict[str, Any]:
    """Run one orchestration across every enabled domain.

    Execution order only follows same-domain edges.  Any job carrying a
    cross-domain edge is recorded as ``blocked_domain`` and never executed:
    it attempts nothing further, adds nothing to the success set and writes
    no watermark.  Each job therefore leaves exactly one attempt row, which
    keeps the per-domain and global counters in step.
    """
    conn = connect()
    try:
        jobs = [j for j in jobs_repo.list_jobs(conn) if j["enabled"]]
        by_name = {j["name"]: j for j in jobs}
        orch_id = orch_repo.create_orchestration(conn)
        success_names: set[str] = set()
        plan = plan_by_domain(jobs)

        for phase in plan:
            for job in phase["jobs"]:
                blocked_edges = cross_domain_edges_for_job(job, by_name)
                if blocked_edges and not allow_cross_domain_execution(blocked_edges):
                    orch_repo.insert_attempt(
                        conn,
                        orchestration_id=orch_id,
                        job_id=job["id"],
                        domain=job["domain"],
                        attempt_no=1,
                        status="blocked_domain",
                        detail=_blocked_detail(blocked_edges),
                    )
                    continue

                local_deps = same_domain_depends_on(job, by_name)
                ready, reason = ready_by_upstream_success(local_deps, success_names)
                if not ready:
                    orch_repo.insert_attempt(
                        conn,
                        orchestration_id=orch_id,
                        job_id=job["id"],
                        domain=job["domain"],
                        attempt_no=1,
                        status="skipped_dep",
                        detail={"reason": reason, "domain": job["domain"]},
                    )
                    continue

                try:
                    result = run_fixture_job(FIXTURES_DIR / job["fixture_path"])
                except Exception as exc:  # noqa: BLE001
                    orch_repo.insert_attempt(
                        conn,
                        orchestration_id=orch_id,
                        job_id=job["id"],
                        domain=job["domain"],
                        attempt_no=1,
                        status="failed",
                        detail={"error": str(exc), "domain": job["domain"]},
                    )
                    continue

                orch_repo.insert_attempt(
                    conn,
                    orchestration_id=orch_id,
                    job_id=job["id"],
                    domain=job["domain"],
                    attempt_no=1,
                    status="success",
                    detail={**result, "domain": job["domain"]},
                )
                wm_repo.set_watermark(
                    conn, job["id"], result["watermark_ts"], result["artifact_rows"]
                )
                success_names.add(job["name"])

        attempts = orch_repo.list_attempts(conn, orch_id)
        report = _build_report(
            conn, orch_id=orch_id, jobs=jobs, attempts=attempts, plan=plan
        )
        status = (
            "ok"
            if report["failed"] == 0 and report["blocked_domain"] == 0
            else "partial"
        )
        orch_repo.finish_orchestration(conn, orch_id, status, report)
        conn.commit()
        return orch_repo.get_orchestration(conn, orch_id) or report
    finally:
        conn.close()


def _build_report(
    conn,
    *,
    orch_id: int,
    jobs: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    plan: list[dict[str, Any]],
) -> dict[str, Any]:
    domains = sorted({j["domain"] for j in jobs})

    # Watermarks written by this run are exactly the successful attempts; each
    # success writes one watermark under the same domain as its attempt row.
    per_domain: list[dict[str, Any]] = []
    for domain in domains:
        domain_jobs = [j for j in jobs if j["domain"] == domain]
        domain_attempts = [a for a in attempts if a["domain"] == domain]
        counts: dict[str, Any] = {
            "domain": domain,
            **_report_counts(domain_attempts),
            "job_total": len(domain_jobs),
            "attempt_count": len(domain_attempts),
            "layer_count": sum(1 for p in plan if p["domain"] == domain),
            "watermarked_jobs": sum(
                1 for a in domain_attempts if a["status"] == "success"
            ),
        }
        per_domain.append(counts)

    slice_totals = {key: sum(d[key] for d in per_domain) for key in _REPORT_SUM_KEYS}

    # Global counters recomputed straight from the run's own rows (jobs, attempt
    # list and successes); the report publishes these, and ``domain_consistent``
    # reports whether the independently summed slices agree with them.
    run_counts = _report_counts(attempts)
    run_counts["job_total"] = len(jobs)
    run_counts["attempt_count"] = len(attempts)
    run_counts["watermarked_jobs"] = run_counts["succeeded"]
    run_counts["layer_count"] = len(plan)

    report = {
        "orchestration_id": orch_id,
        **run_counts,
        # Slice sums are exposed alongside the global figures so the page can
        # show both sides of the reconciliation.
        "domain_totals": slice_totals,
        "domains": per_domain,
        "blocked_edges": cross_domain_edges(jobs),
        "domain_edges": domain_edge_summary(jobs),
        "domain_consistent": _report_sums_match(run_counts, per_domain),
    }
    return report


def _report_sums_match(
    run_counts: dict[str, int], per_domain: list[dict[str, Any]]
) -> bool:
    return all(
        sum(d[key] for d in per_domain) == run_counts[key]
        for key in _REPORT_SUM_KEYS
    )
