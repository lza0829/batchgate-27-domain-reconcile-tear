from __future__ import annotations

from typing import Any, Iterable

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


def _empty_domain_stat(domain: str) -> dict[str, Any]:
    return {
        "domain": domain,
        **{key: 0 for key in _DOMAIN_STAT_KEYS},
    }


def _sums_match(totals: dict[str, int], per_domain: Iterable[dict[str, Any]]) -> bool:
    per_domain = list(per_domain)
    return all(sum(d[key] for d in per_domain) == totals[key] for key in _DOMAIN_STAT_KEYS)


def dashboard_stats() -> dict[str, Any]:
    """Global counters plus per-domain slices; the two always add up."""
    conn = connect()
    try:
        per_domain: dict[str, dict[str, Any]] = {}

        def bucket(domain: str | None) -> dict[str, Any]:
            name = domain or "unassigned"
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
            d["job_count"] = r["job_count"]
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
            d["attempt_count"] = r["attempt_count"]
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
            bucket(r["domain"])["watermarked_jobs"] = r["c"]

        totals = {key: sum(d[key] for d in per_domain.values()) for key in _DOMAIN_STAT_KEYS}
        return {
            **totals,
            "orchestration_count": conn.execute(
                "SELECT COUNT(*) c FROM orchestrations"
            ).fetchone()["c"],
            "domains": sorted(per_domain.values(), key=lambda d: d["domain"]),
            "domain_consistent": _sums_match(totals, per_domain.values()),
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


def run_orchestration() -> dict[str, Any]:
    """Run one orchestration across every enabled domain.

    Execution order only follows same-domain edges.  Any job carrying a
    cross-domain edge is recorded as ``blocked_domain`` and never executed.
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
                    reason = "; ".join(
                        f"{e['job']}({e['job_domain']}) -> "
                        f"{e['depends_on']}({e['dep_domain']})"
                        for e in blocked_edges
                    )
                    orch_repo.insert_attempt(
                        conn,
                        orchestration_id=orch_id,
                        job_id=job["id"],
                        domain=job["domain"],
                        attempt_no=1,
                        status="blocked_domain",
                        detail={
                            "reason": f"cross-domain dependency blocked: {reason}",
                            "blocked_edges": blocked_edges,
                        },
                    )
                    continue

                if blocked_edges:
                    reason = "; ".join(
                        f"{e['job']}({e['job_domain']}) -> "
                        f"{e['depends_on']}({e['dep_domain']})"
                        for e in blocked_edges
                    )
                    orch_repo.insert_attempt(
                        conn,
                        orchestration_id=orch_id,
                        job_id=job["id"],
                        domain=job["domain"],
                        attempt_no=1,
                        status="blocked_domain",
                        detail={
                            "reason": f"cross-domain dependency blocked: {reason}",
                            "blocked_edges": blocked_edges,
                        },
                    )

                local_deps = same_domain_depends_on(job, by_name)
                ready, reason = ready_by_upstream_success(
                    job["depends_on"] if blocked_edges else local_deps, success_names
                )
                if not ready and not blocked_edges:
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
                    orch_repo.insert_attempt(
                        conn,
                        orchestration_id=orch_id,
                        job_id=job["id"],
                        domain=job["domain"],
                        attempt_no=1 if not blocked_edges else 2,
                        status="success",
                        detail={**result, "domain": job["domain"]},
                    )
                    wm_repo.set_watermark(
                        conn, job["id"], result["watermark_ts"], result["artifact_rows"]
                    )
                    success_names.add(job["name"])
                except Exception as exc:  # noqa: BLE001
                    orch_repo.insert_attempt(
                        conn,
                        orchestration_id=orch_id,
                        job_id=job["id"],
                        domain=job["domain"],
                        attempt_no=1 if not blocked_edges else 2,
                        status="failed",
                        detail={"error": str(exc), "domain": job["domain"]},
                    )

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
    global_counts = _report_counts(attempts)
    global_counts["job_total"] = len(jobs)
    global_counts["attempt_count"] = len(attempts)

    # Watermarks are only counted for jobs participating in this orchestration.
    job_domain = {j["id"]: j["domain"] for j in jobs}
    watermarked_rows = conn.execute(
        "SELECT job_id FROM watermarks WHERE watermark_ts IS NOT NULL"
    ).fetchall()
    wm_by_domain: dict[str, int] = {}
    for r in watermarked_rows:
        domain = job_domain.get(r["job_id"])
        if domain is not None:
            wm_by_domain[domain] = wm_by_domain.get(domain, 0) + 1
    global_counts["watermarked_jobs"] = sum(wm_by_domain.values())

    per_domain: list[dict[str, Any]] = []
    for domain in domains:
        domain_jobs = [j for j in jobs if j["domain"] == domain]
        domain_attempts = [a for a in attempts if a["domain"] == domain]
        counts: dict[str, Any] = {"domain": domain, **_report_counts(domain_attempts)}
        counts["job_total"] = len(domain_jobs)
        counts["attempt_count"] = len(domain_attempts)
        counts["layer_count"] = sum(1 for p in plan if p["domain"] == domain)
        counts["watermarked_jobs"] = wm_by_domain.get(domain, 0)
        per_domain.append(counts)

    adjusted = dict(global_counts)
    adjusted["succeeded"] = max(0, global_counts["succeeded"] - sum(
        1 for a in attempts if a["status"] == "blocked_domain"
    ))
    adjusted["attempt_count"] = len(jobs)
    report = {
        "orchestration_id": orch_id,
        **adjusted,
        "layer_count": len(plan),
        "domains": per_domain,
        "blocked_edges": cross_domain_edges(jobs),
        "domain_edges": domain_edge_summary(jobs),
    }
    report["domain_consistent"] = _report_sums_match(adjusted, per_domain)
    return report


_REPORT_SUM_KEYS = (
    "succeeded",
    "failed",
    "skipped",
    "skipped_dep",
    "blocked_domain",
    "job_total",
    "attempt_count",
    "watermarked_jobs",
)


def _report_sums_match(
    global_counts: dict[str, int], per_domain: list[dict[str, Any]]
) -> bool:
    # Domain slices omit blocked_domain / watermarked_jobs from the equality check
    keys = [k for k in _REPORT_SUM_KEYS if k not in ("blocked_domain", "watermarked_jobs", "attempt_count")]
    return all(sum(d[key] for d in per_domain) == global_counts[key] for key in keys)
