"""Domain isolation rules for the job dependency graph.

One rule set is shared by configuration saves and orchestration readiness:

* an edge whose endpoints live in different domains is a cross-domain edge;
* a job carrying such an edge is never executable (``blocked_domain``);
* a domain move that would create such an edge is refused at save time;
* edges already persisted in the database stay queryable as dirty data, but
  they can never make a job run or advance a watermark.
"""

from __future__ import annotations

from typing import Any, Iterable


def dependency_edges(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return known dependency edges (unknown dependency names are skipped)."""
    by_name = {j["name"]: j for j in jobs}
    edges: list[dict[str, Any]] = []
    for job in jobs:
        for dep_name in job["depends_on"]:
            dep = by_name.get(dep_name)
            if dep is None:
                continue
            edges.append(_edge(job, job["domain"], dep_name, dep["domain"]))
    return edges


def cross_domain_edges(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """All known edges whose endpoints live in different domains."""
    return [e for e in dependency_edges(jobs) if _is_cross_domain(e)]


def cross_domain_edges_for_job(
    job: dict[str, Any], all_jobs_by_name: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Cross-domain edges from this job to its declared upstreams."""
    edges: list[dict[str, Any]] = []
    for dep_name in job["depends_on"]:
        dep = all_jobs_by_name.get(dep_name)
        if dep is None:
            continue
        edge = _edge(job, job["domain"], dep_name, dep["domain"])
        if _is_cross_domain(edge):
            edges.append(edge)
    return edges


def cross_domain_conflicts_for_change(
    jobs: Iterable[dict[str, Any]], job_id: int, new_domain: str
) -> list[dict[str, Any]]:
    """Cross-domain edges that moving ``job_id`` to ``new_domain`` would create.

    Both directions are checked: the moved job's own upstreams and every
    downstream job that depends on it.  An empty result means the save is safe.
    """
    jobs = list(jobs)
    target = next((j for j in jobs if j["id"] == job_id), None)
    if target is None or target["domain"] == new_domain:
        return []
    return _preview_conflicts(jobs, target, new_domain)


def allow_cross_domain_execution(edges: list[dict[str, Any]]) -> bool:
    """Whether cross-domain edges permit execution in this run.

    They never do, even when the upstream job already succeeded in another
    orchestration phase.  Configuration and orchestration both consult this
    same prohibition.
    """
    return not any(edges)


def _is_cross_domain(edge: dict[str, Any]) -> bool:
    return edge["job_domain"] != edge["dep_domain"]


def _preview_conflicts(
    jobs: list[dict[str, Any]], target: dict[str, Any], new_domain: str
) -> list[dict[str, Any]]:
    by_name = {j["name"]: j for j in jobs}
    conflicts: list[dict[str, Any]] = []
    for dep_name in target["depends_on"]:
        dep = by_name.get(dep_name)
        if dep is None:
            continue
        edge = _edge(target, new_domain, dep_name, dep["domain"])
        if _is_cross_domain(edge):
            conflicts.append(edge)
    for other in jobs:
        if other["id"] == target["id"]:
            continue
        if target["name"] not in other["depends_on"]:
            continue
        edge = _edge(other, other["domain"], target["name"], new_domain)
        if _is_cross_domain(edge):
            conflicts.append(edge)
    return _dedupe(conflicts)


def _edge(
    job: dict[str, Any], job_domain: str, dep_name: str, dep_domain: str
) -> dict[str, Any]:
    return {
        "job": job["name"],
        "job_domain": job_domain,
        "depends_on": dep_name,
        "dep_domain": dep_domain,
    }


def _dedupe(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for e in edges:
        key = (e["job"], e["job_domain"], e["depends_on"], e["dep_domain"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
