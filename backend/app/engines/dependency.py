"""Base dependency check plus domain-scoped planning.

The dependency gate only ever considers upstream jobs inside the same business
domain.  Cross-domain edges are not ordering constraints: they are refused by
the domain guard and can never produce a successful execution.
"""

from __future__ import annotations

from typing import Any

from app.engines.domain_guard import dependency_edges


def ready_by_upstream_success(
    depends_on: list[str],
    success_job_names: set[str],
) -> tuple[bool, str]:
    missing = [name for name in depends_on if name not in success_job_names]
    if missing:
        return False, f"upstream not success: {', '.join(missing)}"
    return True, "ok"


def topological_layers(
    jobs: list[dict[str, Any]], deps: dict[str, list[str]] | None = None
) -> list[list[dict[str, Any]]]:
    """Group jobs into layers by declared depends_on names (best-effort).

    ``deps`` optionally overrides the dependency list used for ordering, so the
    orchestration can order strictly inside a domain.
    """
    by_name = {j["name"]: j for j in jobs}
    remaining = set(by_name)
    done: set[str] = set()
    layers: list[list[dict[str, Any]]] = []
    while remaining:
        layer = []
        for name in sorted(remaining):
            job_deps = deps[name] if deps is not None else by_name[name]["depends_on"]
            if all(d in done or d not in by_name for d in job_deps):
                layer.append(by_name[name])
        if not layer:
            # cycle — dump rest as final layer for visibility
            layers.append([by_name[n] for n in sorted(remaining)])
            break
        layers.append(layer)
        for j in layer:
            remaining.remove(j["name"])
            done.add(j["name"])
    return layers


def same_domain_depends_on(
    job: dict[str, Any], by_name: dict[str, dict[str, Any]]
) -> list[str]:
    """Upstreams usable for ordering/readiness inside ``job``'s own domain.

    Unknown dependency names are kept: they can never be satisfied, so the job
    stays ``skipped_dep`` instead of silently running.  Known upstreams in
    another domain are dropped here — they are refused by the domain guard
    before readiness is ever consulted.
    """
    return [
        dep_name
        for dep_name in job["depends_on"]
        if dep_name not in by_name or by_name[dep_name]["domain"] == job["domain"]
    ]


def plan_by_domain(
    jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return execution layers grouped per domain in stable domain order.

    Each item is ``{"domain": str, "layer": int, "jobs": [...]}``.  Layer
    numbering restarts per domain and only same-domain edges are used for
    ordering, so one orchestration can cover several domains without any
    cross-domain edge influencing execution order.
    """
    by_name = {j["name"]: j for j in jobs}
    domains = sorted({j["domain"] for j in jobs})
    plan: list[dict[str, Any]] = []
    for domain in domains:
        domain_jobs = [j for j in jobs if j["domain"] == domain]
        deps = {j["name"]: same_domain_depends_on(j, by_name) for j in domain_jobs}
        for layer_no, layer in enumerate(topological_layers(domain_jobs, deps), start=1):
            plan.append({"domain": domain, "layer": layer_no, "jobs": layer})
    return plan


def domain_edge_summary(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-domain edge counts (local edges vs blocked cross-domain edges)."""
    edges = dependency_edges(jobs)
    domains = sorted({j["domain"] for j in jobs})
    out = []
    for domain in domains:
        own = [e for e in edges if e["job_domain"] == domain]
        cross = [e for e in own if e["job_domain"] != e["dep_domain"]]
        out.append(
            {
                "domain": domain,
                "dependency_edges": len(own),
                "local_edges": len(own) - len(cross),
                "cross_domain_edges": len(cross),
            }
        )
    return out
