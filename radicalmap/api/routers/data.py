"""Read endpoints. Thin — all SQL lives in api/query.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import query as Q

router = APIRouter(tags=["read"])


@router.get("/overview")
def overview() -> dict:
    return Q.overview()


@router.get("/items")
def items(q: str | None = None, account: int | None = None, tag: int | None = None,
          sound: int | None = None, findings: bool = False, sort: str | None = None,
          limit: int = Query(Q.DEFAULT_LIMIT, ge=1, le=Q.MAX_LIMIT),
          offset: int = Query(0, ge=0)) -> dict:
    try:
        return Q.items(q=q, account=account, tag=tag, sound=sound,
                       has_findings=findings, sort=sort, limit=limit, offset=offset)
    except Q.BadQuery as exc:
        raise HTTPException(400, f"bad search expression: {exc}") from exc


@router.get("/items/{item_id}")
def item_detail(item_id: int) -> dict:
    d = Q.item_detail(item_id)
    if d is None:
        raise HTTPException(404, "no such item")
    return d


@router.get("/classifications")
def classifications(stage: str | None = None, verdict: str | None = None,
                    domain: str | None = None, harm: str | None = None,
                    stance: str | None = None, run: int | None = None,
                    target_type: str | None = None, findings: bool = False,
                    sort: str | None = None,
                    limit: int = Query(Q.DEFAULT_LIMIT, ge=1, le=Q.MAX_LIMIT),
                    offset: int = Query(0, ge=0)) -> dict:
    return Q.classifications(stage=stage, verdict=verdict, domain=domain, harm=harm,
                             stance=stance, run=run, target_type=target_type,
                             findings_only=findings, sort=sort, limit=limit,
                             offset=offset)


@router.get("/classifications/facets")
def facets() -> dict:
    return Q.classification_facets()


@router.get("/target/{target_type}/{target_id}")
def target(target_type: str, target_id: int) -> dict:
    t = Q.classification_target(target_type, target_id)
    if t is None:
        raise HTTPException(404, "no such target")
    return t


@router.get("/nodes")
def nodes(node_type: str | None = None, community: int | None = None,
          sort: str | None = None,
          limit: int = Query(Q.DEFAULT_LIMIT, ge=1, le=Q.MAX_LIMIT),
          offset: int = Query(0, ge=0)) -> dict:
    return Q.nodes(node_type=node_type, community=community, sort=sort,
                   limit=limit, offset=offset)


@router.get("/nodes/{node_type}/{node_id}/edges")
def node_edges(node_type: str, node_id: int,
               limit: int = Query(Q.DEFAULT_LIMIT, ge=1, le=Q.MAX_LIMIT)) -> list[dict]:
    return Q.node_edges(node_type, node_id, limit=limit)


@router.get("/communities")
def communities() -> list[dict]:
    return Q.communities()


@router.get("/edge-types")
def edge_types() -> list[dict]:
    return Q.edge_types()


@router.get("/lexicon")
def lexicon(version: int | None = None, tier: str | None = None,
            domain: str | None = None, q: str | None = None,
            limit: int = Query(Q.DEFAULT_LIMIT, ge=1, le=Q.MAX_LIMIT),
            offset: int = Query(0, ge=0)) -> dict:
    return Q.lexicon(version=version, tier=tier, domain=domain, q=q,
                     limit=limit, offset=offset)


@router.get("/taxonomy")
def taxonomy() -> dict:
    return Q.taxonomy()


@router.get("/jobs")
def jobs(status: str | None = None,
         limit: int = Query(Q.DEFAULT_LIMIT, ge=1, le=Q.MAX_LIMIT),
         offset: int = Query(0, ge=0)) -> dict:
    return Q.jobs(status=status, limit=limit, offset=offset)


@router.get("/propagation")
def propagation(limit: int = Query(Q.DEFAULT_LIMIT, ge=1, le=Q.MAX_LIMIT),
                offset: int = Query(0, ge=0)) -> dict:
    return Q.propagation(limit=limit, offset=offset)
