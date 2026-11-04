"""流程型测试的公共步骤。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core import clock
from tests.factory import LAT, LNG, World

TOMORROW_14 = datetime(2026, 6, 16, 14, 0)


def create_order(api, world: World, *, elder=None, item=None, start=None, user="family_a1", **extra) -> dict:
    payload = {"elder_id": elder or world.elder_a1, "item_id": item or world.item_bath,
               "scheduled_start": (start or TOMORROW_14).isoformat(), **extra}
    return api.ok("POST", "/orders", user, json=payload)


def accepted_order(api, world: World, *, grid="grid_a1", note=None, **kwargs) -> dict:
    order = create_order(api, world, **kwargs)
    return api.ok("POST", f"/orders/{order['id']}/accept", grid, json={"note": note})


def offered_order(api, world: World, *, caregiver=None, grid="grid_a1", **kwargs) -> tuple[dict, dict]:
    order = accepted_order(api, world, grid=grid, **kwargs)
    rec = api.ok("POST", f"/orders/{order['id']}/recommend", grid)
    payload = {"round_id": rec["round_id"]}
    if caregiver:
        payload["caregiver_id"] = caregiver
    assignment = api.ok("POST", f"/orders/{order['id']}/assign", grid, json=payload)
    return order, assignment


def dispatched_order(api, world: World, *, cg_user="cg_near", caregiver=None, **kwargs) -> dict:
    order, assignment = offered_order(api, world, caregiver=caregiver or world.c_near, **kwargs)
    api.ok("POST", f"/offers/{assignment['offer_id']}/accept", cg_user)
    return order


def in_service_order(api, world: World, *, cg_user="cg_near", start=None, **kwargs) -> dict:
    start = start or TOMORROW_14
    order = dispatched_order(api, world, cg_user=cg_user, start=start, **kwargs)
    clock.freeze(start)
    api.ok("POST", f"/orders/{order['id']}/check-in", cg_user, json={"lat": LAT, "lng": LNG})
    return order


def completed_order(api, world: World, *, cg_user="cg_near", minutes=60, start=None, metrics=None, **kwargs) -> dict:
    start = start or TOMORROW_14
    order = in_service_order(api, world, cg_user=cg_user, start=start, **kwargs)
    detail = api.ok("GET", f"/orders/{order['id']}", cg_user)
    items = [{"name": c["name"], "done": True} for c in detail["execution"]["checklist"]]
    api.ok("PUT", f"/orders/{order['id']}/checklist", cg_user, json={"items": items})
    clock.freeze(start + timedelta(minutes=minutes))
    api.ok("POST", f"/orders/{order['id']}/check-out", cg_user, json={"summary": "服务完成", "metrics": metrics or []})
    return order
