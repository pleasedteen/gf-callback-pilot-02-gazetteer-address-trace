"""查档核心引擎（纯函数，不碰数据库）。

规则要点：
- 名称按原字匹配，取落款当天有效的街名/区名；
- 日期区间含首日、不含末日；门牌范围两端都含；
- 落款当天从当日地址归属查起，之后的划转在生效当天就算数；
- 同一天的划转都按当天变更前的归属算（快照语义）；
- 一条候选在前一次划转后只剩部分门牌可行，后面的划转只能继续用这部分；
- 原区仍存在时没划走的地址留在原区；原区撤销且无去向记录的部分记为断档。
"""
from __future__ import annotations

from typing import Any

from . import dates, intervals
from .models import Catalog, Membership, Transfer


# ---------------------------------------------------------------- 名称与归属

def _display_district(catalog: Catalog, district_id: str, day: str) -> dict[str, Any]:
    name = catalog.district_name_on(district_id, day)
    if name is None:
        name = catalog.district_latest_name(district_id)
        return {"district_id": district_id, "name": name, "name_current_on_day": False}
    return {"district_id": district_id, "name": name, "name_current_on_day": True}


def _display_street(catalog: Catalog, street_id: str, day: str) -> dict[str, Any]:
    name = catalog.street_name_on(street_id, day)
    if name is None:
        # 改名后编号沿用：取最近的历史名
        names = sorted((sn for sn in catalog.streets if sn.street_id == street_id),
                       key=lambda sn: sn.valid_from)
        name = names[-1].name if names else None
        return {"street_id": street_id, "name": name, "name_current_on_day": False}
    return {"street_id": street_id, "name": name, "name_current_on_day": True}


def resolve_initial(catalog: Catalog, street_name: str, district_name: str | None,
                    house: int | None, day: str) -> dict[str, Any]:
    """落款当日的候选解析。返回 {candidates: [...], unmatched: None|{...}}。"""
    street_ids = catalog.street_ids_named(street_name, day)
    if not street_ids:
        return {"candidates": [],
                "unmatched": {"reason": "street",
                              "detail": f"落款日 {day} 没有按原字匹配到有效街名「{street_name}」"}}

    wanted_district = None
    if district_name:
        wanted_district = catalog.district_id_named(district_name, day)
        if wanted_district is None:
            return {"candidates": [],
                    "unmatched": {"reason": "district",
                                  "detail": f"落款日 {day} 没有按原字匹配到有效区名「{district_name}」"}}

    constraint = (house, house) if house is not None else None
    found: list[dict[str, Any]] = []
    any_membership_on_streets = False
    any_membership_in_district = False
    any_house_hit = False

    for m in catalog.memberships:
        if m.street_id not in street_ids:
            continue
        if not dates.effective_on(day, m.valid_from, m.valid_to):
            continue
        any_membership_on_streets = True
        if wanted_district is not None and m.district_id != wanted_district:
            continue
        if wanted_district is not None:
            any_membership_in_district = True
        miv = (m.lo, m.hi)
        if constraint is not None:
            hit = intervals.intersection(miv, constraint)
            if hit is None:
                continue
            any_house_hit = True
            ivs = [hit]
        else:
            # 没有门牌：保留符合的门牌范围
            ivs = [miv]
        found.append({
            "street_id": m.street_id,
            "district_id": m.district_id,
            "intervals": ivs,
            "membership_ids": [m.membership_id],
        })

    if found:
        return {"candidates": found, "unmatched": None}

    if not any_membership_on_streets:
        detail = f"落款日 {day} 目录中没有「{street_name}」的门牌归属记录"
        reason = "membership"
    elif wanted_district is not None and not any_membership_in_district:
        detail = (f"落款日 {day}「{street_name}」没有门牌属于区「{district_name}」"
                  f"（无区名时可保留同名街的其他候选）")
        reason = "district"
    elif constraint is not None and not any_house_hit:
        detail = f"落款日 {day} 的门牌归属范围都不包含 {house} 号"
        reason = "house"
    else:  # 日期内无有效归属段
        detail = f"落款日 {day} 没有有效归属记录"
        reason = "membership"
    return {"candidates": [], "unmatched": {"reason": reason, "detail": detail}}


# ---------------------------------------------------------------- 划转模拟

def _new_piece(cand_index: int, street_id: str, district_id: str, ivs,
               membership_ids: list[str]) -> dict[str, Any]:
    return {
        "cand_index": cand_index,
        "street_id": street_id,
        "district_id": district_id,
        "intervals": list(ivs),
        "membership_ids": list(membership_ids),
        "broken": None,
    }


def simulate(catalog: Catalog, initial: list[dict[str, Any]],
             signing_day: str, query_day: str) -> tuple[list[dict[str, Any]],
                                                        dict[int, list[dict[str, Any]]]]:
    """从落款当日候选出发，模拟到 query_day（含当天生效的划转）。

    返回 (最终片段列表, 每个候选的事件流水)。
    """
    pieces = [_new_piece(i, c["street_id"], c["district_id"], c["intervals"],
                         c["membership_ids"])
              for i, c in enumerate(initial)]
    events: dict[int, list[dict[str, Any]]] = {i: [] for i in range(len(initial))}

    if dates.as_orderable(query_day) < dates.as_orderable(signing_day):
        raise ValueError("查询日期须在落款当天或之后")

    # 划转在生效当天就算数。落款当天的划转仍在模拟范围内：当查询日就是落款日
    # 时，effective_on <= query_day 把它包含进来，而下面的快照豁免保证当天新
    # 划入的部分按变更前归属展示；查询日晚于落款日时，这些划转正常生效。
    transfers = [t for t in catalog.transfers
                 if dates.as_orderable(t.effective_on) <= dates.as_orderable(query_day)]
    days: dict[str, list[Transfer]] = {}
    for t in transfers:
        days.setdefault(t.effective_on, []).append(t)

    for day in sorted(days, key=lambda d: dates.as_orderable(d)):
        if day == signing_day and day == query_day:
            # 落款当天查询：当天划转一律按变更前归属算，整天不应用，
            # 该日状态完全由当日归属（membership）承载。
            continue
        day_transfers = days[day]

        # 快照语义：当天所有划转都基于当天变更前的片段判定。
        # 先为每个 (候选, 街, 区) 汇总当天要划入的门牌；这些门牌在本次循环里
        # 视为“当天到达”，不再被同区旧划转回头切割。其余片段（含前一次划转后
        # 只剩部分门牌的片段）按当天起始状态正常适用划转，片段彼此独立。
        arrivals: dict[tuple[int, str, str], list[intervals.Interval]] = {}
        for t in day_transfers:
            for p in pieces:
                if p["broken"] is not None:
                    continue
                if p["street_id"] != t.street_id or p["district_id"] != t.from_district_id:
                    continue
                for iv in p["intervals"]:
                    hit = intervals.intersection(iv, t.interval)
                    if hit:
                        key = (p["cand_index"], t.street_id, t.to_district_id)
                        arrivals.setdefault(key, []).append(hit)
        incoming = {k: intervals.normalize(v) for k, v in arrivals.items()}

        snapshot = [dict(p, intervals=list(p["intervals"])) for p in pieces]
        next_pieces: list[dict[str, Any]] = []

        for p in snapshot:
            if p["broken"] is not None:
                next_pieces.append(p)  # 已断档的部分不参与后续划转
                continue
            key = (p["cand_index"], p["street_id"], p["district_id"])
            if key in incoming:
                # 当天新划入本区的门牌不被同区旧划转回头切割；其余门牌正常适用。
                # 本来就在该区、与划入无关的片段不受影响。
                arrived_ivs = incoming[key]
                exempt = []
                subject = list(p["intervals"])
                for iv in arrived_ivs:
                    for src in p["intervals"]:
                        hit = intervals.intersection(src, iv)
                        if hit:
                            exempt.append(hit)
                for iv in intervals.normalize(exempt):
                    subject = intervals.subtract(subject, iv)
                if exempt:
                    next_pieces.append(_new_piece(
                        p["cand_index"], p["street_id"], p["district_id"],
                        intervals.normalize(exempt), p["membership_ids"]))
                if not subject:
                    continue
                p = _new_piece(p["cand_index"], p["street_id"], p["district_id"],
                               subject, p["membership_ids"])

            relevant: list[Transfer] = [
                t for t in day_transfers
                if t.street_id == p["street_id"]
                and t.from_district_id == p["district_id"]
                and any(intervals.intersection(iv, t.interval)
                        for iv in p["intervals"])]
            if not relevant:
                next_pieces.append(p)
                continue

            # 每条划转按变更前片段求交（片段粒度保留，不预先合并）
            moved: list[tuple[str, intervals.Interval, str]] = []
            for t in relevant:
                for iv in p["intervals"]:
                    hit = intervals.intersection(iv, t.interval)
                    if hit:
                        moved.append((t.to_district_id, hit, t.transfer_id))

            cut_union = intervals.normalize([iv for _, iv, _ in moved])
            remaining = list(p["intervals"])
            for cut in cut_union:
                remaining = intervals.subtract(remaining, cut)

            ev = {
                "on": day,
                "transfers": [
                    {"transfer_id": t.transfer_id, "street_id": t.street_id,
                     "lo": t.lo, "hi": t.hi,
                     "from_district_id": t.from_district_id,
                     "to_district_id": t.to_district_id}
                    for t in relevant],
                "moved": [],
                "remaining_after": remaining,
                "broken": None,
            }

            # 按目标区聚合（同区重复划转合并展示，片段粒度仍保留）
            by_target: dict[str, list[intervals.Interval]] = {}
            tids_by_target: dict[str, list[str]] = {}
            for to_district, iv, tid in moved:
                by_target.setdefault(to_district, []).append(iv)
                tids_by_target.setdefault(to_district, [])
                if tid not in tids_by_target[to_district]:
                    tids_by_target[to_district].append(tid)
            for to_district, ivs in by_target.items():
                next_pieces.append(_new_piece(
                    p["cand_index"], p["street_id"], to_district,
                    intervals.normalize(ivs), p["membership_ids"]))
                ev["moved"].append({
                    "to_district_id": to_district,
                    "intervals": intervals.normalize(ivs),
                    "transfer_ids": tids_by_target[to_district],
                })

            if remaining:
                next_pieces.append(_new_piece(p["cand_index"], p["street_id"],
                                              p["district_id"], remaining,
                                              p["membership_ids"]))
            events[p["cand_index"]].append(ev)

        # 当日结束：区在当天撤销时，仍留在该区且无去向的部分记为断档
        for p in next_pieces:
            if p["broken"] is not None:
                continue
            abolished = catalog.district_abolished_on(p["district_id"])
            if abolished == day:
                p["broken"] = {"on": day, "district_id": p["district_id"]}
                if events[p["cand_index"]] and events[p["cand_index"]][-1]["on"] == day:
                    events[p["cand_index"]][-1]["broken"] = dict(p["broken"])
                else:
                    events[p["cand_index"]].append({
                        "on": day, "transfers": [], "moved": [],
                        "remaining_after": list(p["intervals"]),
                        "broken": dict(p["broken"]),
                    })

        pieces = next_pieces

    return pieces, events


# ---------------------------------------------------------------- 档案正查

def query_archive(catalog: Catalog, archive: dict[str, Any], query_day: str) -> dict[str, Any]:
    signing_day = archive["signing_date"]
    if dates.as_orderable(query_day) < dates.as_orderable(signing_day):
        raise ValueError(f"查询日期 {query_day} 早于落款日 {signing_day}，须在落款当天或之后")

    res = resolve_initial(catalog, archive["street_name"], archive.get("district_name"),
                          archive.get("house_number"), signing_day)

    out: dict[str, Any] = {
        "archive_id": archive["archive_id"],
        "revision_id": catalog.revision_id,
        "signing_day": signing_day,
        "query_day": query_day,
        "address_text": archive["address_text"],
        "recorded": {
            "street_name": archive["street_name"],
            "district_name": archive.get("district_name"),
            "house_number": archive.get("house_number"),
        },
        "resolved": res["unmatched"] is None,
        "unmatched": res["unmatched"],
        "candidates": [],
        "final": [],
        "unknown": [],
    }
    if res["unmatched"] is not None:
        return out

    pieces, events = simulate(catalog, res["candidates"], signing_day, query_day)

    for i, c in enumerate(res["candidates"]):
        lineage = [p for p in pieces if p["cand_index"] == i]
        current = []
        for p in lineage:
            item = {
                "street": _display_street(catalog, p["street_id"], query_day),
                "district": _display_district(catalog, p["district_id"], query_day),
                "intervals": p["intervals"],
                "membership_ids": p["membership_ids"],
                "broken": p["broken"],
            }
            current.append(item)
        out["candidates"].append({
            "index": i,
            "street_at_signing": _display_street(catalog, c["street_id"], signing_day),
            "district_at_signing": _display_district(catalog, c["district_id"], signing_day),
            "initial_intervals": c["intervals"],
            "membership_ids": c["membership_ids"],
            "events": events[i],
            "current": current,
        })

    # 最终归属：按 (街, 区) 聚合，保留来自哪些初始候选（歧义可见）
    live_key: dict[tuple[str, str], dict[str, Any]] = {}
    broken_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for p in pieces:
        if p["broken"] is None:
            key = (p["street_id"], p["district_id"])
            agg = live_key.setdefault(key, {
                "street": _display_street(catalog, p["street_id"], query_day),
                "district": _display_district(catalog, p["district_id"], query_day),
                "intervals": [], "from_candidates": set()})
            agg["intervals"] = intervals.normalize(agg["intervals"] + p["intervals"])
            agg["from_candidates"].add(p["cand_index"])
        else:
            b = p["broken"]
            key = (p["street_id"], b["district_id"], b["on"])
            agg = broken_key.setdefault(key, {
                "street": _display_street(catalog, p["street_id"], query_day),
                "district": _display_district(catalog, b["district_id"], b["on"]),
                "broken_on": b["on"], "intervals": [], "from_candidates": set()})
            agg["intervals"] = intervals.normalize(agg["intervals"] + p["intervals"])
            agg["from_candidates"].add(p["cand_index"])

    out["final"] = [{**v, "from_candidates": sorted(v["from_candidates"])}
                    for v in sorted(live_key.values(),
                                    key=lambda v: (v["street"]["street_id"],
                                                   v["district"]["district_id"]))]
    out["unknown"] = [{**v, "from_candidates": sorted(v["from_candidates"])}
                      for v in sorted(broken_key.values(),
                                      key=lambda v: (v["street"]["street_id"],
                                                     v["broken_on"]))]
    return out


# ---------------------------------------------------------------- 区反查

def reverse_lookup(catalog: Catalog, archives: list[dict[str, Any]],
                   district_ref: str, day: str) -> dict[str, Any]:
    # 允许用区编号或当天有效的区名（原字匹配）
    did = district_ref
    if not any(d.district_id == district_ref for d in catalog.district_life):
        resolved = catalog.district_id_named(district_ref, day)
        if resolved is None:
            raise ValueError(f"{day} 没有按原字匹配到区「{district_ref}」，也不是已知区编号")
        did = resolved

    matches: list[dict[str, Any]] = []
    unresolved_archives: list[dict[str, str]] = []
    for a in sorted(archives, key=lambda x: x["archive_id"]):
        if dates.as_orderable(day) < dates.as_orderable(a["signing_date"]):
            continue  # 档案尚未形成
        r = query_archive(catalog, a, day)
        if not r["resolved"]:
            unresolved_archives.append({"archive_id": a["archive_id"],
                                        "detail": r["unmatched"]["detail"]})
            continue
        in_target = [f for f in r["final"] if f["district"]["district_id"] == did]
        if not in_target:
            continue
        other_districts = sorted({f["district"]["district_id"] for f in r["final"]
                                  if f["district"]["district_id"] != did})
        matches.append({
            "archive_id": a["archive_id"],
            "address_text": a["address_text"],
            "matched_intervals": intervals.normalize(
                [iv for f in in_target for iv in f["intervals"]]),
            "may_belong_elsewhere": bool(other_districts),
            "other_district_ids": other_districts,
            "has_unknown_part": bool(r["unknown"]),
        })

    return {"district_id": did,
            "district": _display_district(catalog, did, day),
            "day": day,
            "revision_id": catalog.revision_id,
            "count": len(matches),
            "matches": matches,
            "unresolved_archives": unresolved_archives}
