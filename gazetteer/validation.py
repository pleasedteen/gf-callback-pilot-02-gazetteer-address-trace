"""目录修订启用前的校验。

拒绝条件：一份修订把同一天、同一旧区、同一条街的同一门牌划给两个不同区。
同区重复（划给同一个区）不冲突，允许启用。
"""
from __future__ import annotations

from dataclasses import dataclass

from .intervals import overlap
from .models import Catalog, Transfer


@dataclass
class ValidationReport:
    ok: bool
    conflicts: list[dict]
    errors: list[str]


def validate_catalog(catalog: Catalog) -> ValidationReport:
    errors: list[str] = []
    conflicts: list[dict] = []

    # 基础完整性检查
    street_ids = {sn.street_id for sn in catalog.streets}
    district_ids = {d.district_id for d in catalog.district_life}
    name_district_ids = {dn.district_id for dn in catalog.districts}

    for m in catalog.memberships:
        if m.street_id not in street_ids:
            errors.append(f"归属 {m.membership_id} 引用了未知街巷 {m.street_id}")
        if m.district_id not in district_ids:
            errors.append(f"归属 {m.membership_id} 引用了未知行政区 {m.district_id}")
    for t in catalog.transfers:
        if t.street_id not in street_ids:
            errors.append(f"划转 {t.transfer_id} 引用了未知街巷 {t.street_id}")
        for label, did in (("旧区", t.from_district_id), ("新区", t.to_district_id)):
            if did not in district_ids:
                errors.append(f"划转 {t.transfer_id} 引用了未知{label} {did}")
    for did in name_district_ids - district_ids:
        errors.append(f"区名记录引用了未建区的行政区 {did}")

    # 同日同旧区同街，门牌重叠但目标区不同 -> 冲突
    buckets: dict[tuple[str, str, str], list[Transfer]] = {}
    for t in catalog.transfers:
        buckets.setdefault((t.effective_on, t.from_district_id, t.street_id), []).append(t)

    for (day, from_d, street_id), group in sorted(buckets.items()):
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.to_district_id == b.to_district_id:
                    continue  # 同区划转，不冲突
                if overlap(a.interval, b.interval):
                    conflicts.append({
                        "effective_on": day,
                        "from_district_id": from_d,
                        "street_id": street_id,
                        "house_overlap": _overlap_desc(a.interval, b.interval),
                        "transfers": [
                            {"transfer_id": a.transfer_id,
                             "to_district_id": a.to_district_id, "lo": a.lo, "hi": a.hi},
                            {"transfer_id": b.transfer_id,
                             "to_district_id": b.to_district_id, "lo": b.lo, "hi": b.hi},
                        ],
                    })

    return ValidationReport(ok=not conflicts and not errors,
                            conflicts=conflicts, errors=errors)


def _overlap_desc(a, b) -> str:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return f"{lo}-{hi}号" if lo != hi else f"{lo}号"
