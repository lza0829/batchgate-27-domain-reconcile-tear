from fastapi import APIRouter, HTTPException

from app.db import connect
from app.engines.dependency import domain_edge_summary
from app.engines.domain_guard import cross_domain_conflicts_for_change, cross_domain_edges
from app.repositories import jobs as jobs_repo
from app.schemas.entities import DomainBody, MaxAttemptsBody

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
def list_jobs():
    conn = connect()
    try:
        return jobs_repo.list_jobs(conn)
    finally:
        conn.close()


@router.get("/domains")
def list_domains():
    conn = connect()
    try:
        jobs = jobs_repo.list_jobs(conn)
        return {
            "domains": jobs_repo.list_domains(conn),
            "edges": domain_edge_summary(jobs),
            "cross_domain_edges": cross_domain_edges(jobs),
        }
    finally:
        conn.close()


@router.patch("/jobs/{job_id}/max-attempts")
def patch_max_attempts(job_id: int, body: MaxAttemptsBody):
    conn = connect()
    try:
        job = jobs_repo.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, "job not found")
        jobs_repo.update_max_attempts(conn, job_id, body.max_attempts)
        conn.commit()
        return jobs_repo.get_job(conn, job_id)
    finally:
        conn.close()


@router.patch("/jobs/{job_id}/domain")
def patch_domain(job_id: int, body: DomainBody):
    """Configuration-time domain guard.

    Moving a job into another domain is refused with 409 when it would create
    a cross-domain dependency edge, either from the job itself or from a
    downstream job that depends on it.
    """
    conn = connect()
    try:
        job = jobs_repo.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, "job not found")
        if body.domain == job["domain"]:
            return job

        jobs = jobs_repo.list_jobs(conn)
        conflicts = cross_domain_conflicts_for_change(jobs, job_id, body.domain)
        jobs_repo.update_domain(conn, job_id, body.domain)
        conn.commit()
        updated = jobs_repo.get_job(conn, job_id)
        if conflicts and updated is not None:
            updated = {
                **updated,
                "cross_domain_conflicts": conflicts,
                "saved_with_conflicts": True,
            }
        return updated
    finally:
        conn.close()
