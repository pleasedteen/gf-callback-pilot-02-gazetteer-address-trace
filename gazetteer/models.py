"""目录领域模型（纯数据，与存储解耦）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .intervals import Interval


@dataclass(frozen=True)
class StreetName:
    street_id: str
    name: str          # 名称按原字匹配，原样保存
    valid_from: str    # 含
    valid_to: str      # 不含；至今为 dates.INF


@dataclass(frozen=True)
class DistrictName:
    district_id: str
    name: str
    valid_from: str
    valid_to: str


@dataclass(frozen=True)
class District:
    district_id: str
    established_on: str
    abolished_on: str | None  # 撤销日；None 表示仍存在


@dataclass(frozen=True)
class Membership:
    """某段日期内某条街的哪些门牌属于哪个区（基期归属）。"""
    membership_id: str
    street_id: str
    district_id: str
    lo: int
    hi: int
    valid_from: str
    valid_to: str


@dataclass(frozen=True)
class Transfer:
    """撤并/划转：某日把某旧区某条街的一段门牌划到新区。

    划转资料中的街巷编号和门牌已统一到同一套旧号。
    """
    transfer_id: str
    effective_on: str
    street_id: str
    lo: int
    hi: int
    from_district_id: str
    to_district_id: str

    @property
    def interval(self) -> Interval:
        return (self.lo, self.hi)


@dataclass
class Catalog:
    revision_id: str
    note: str
    streets: list[StreetName] = field(default_factory=list)
    districts: list[DistrictName] = field(default_factory=list)
    district_life: list[District] = field(default_factory=list)
    memberships: list[Membership] = field(default_factory=list)
    transfers: list[Transfer] = field(default_factory=list)

    # ---- 名称解析（按原字匹配，取落款当天有效的名称）----
    def street_ids_named(self, name: str, day: str) -> list[str]:
        ids = {sn.street_id for sn in self.streets
               if sn.name == name and _on(day, sn.valid_from, sn.valid_to)}
        return sorted(ids)

    def district_id_named(self, name: str, day: str) -> str | None:
        hits = [dn.district_id for dn in self.districts
                if dn.name == name and _on(day, dn.valid_from, dn.valid_to)]
        return hits[0] if hits else None

    def street_name_on(self, street_id: str, day: str) -> str | None:
        names = [sn.name for sn in self.streets
                 if sn.street_id == street_id and _on(day, sn.valid_from, sn.valid_to)]
        return names[0] if names else None

    def district_name_on(self, district_id: str, day: str) -> str | None:
        names = [dn.name for dn in self.districts
                 if dn.district_id == district_id
                 and _on(day, dn.valid_from, dn.valid_to)]
        return names[0] if names else None

    def district_latest_name(self, district_id: str) -> str | None:
        names = sorted((dn for dn in self.districts
                        if dn.district_id == district_id),
                       key=lambda dn: dn.valid_from)
        return names[-1].name if names else None

    def district_abolished_on(self, district_id: str) -> str | None:
        for d in self.district_life:
            if d.district_id == district_id:
                return d.abolished_on
        return None


def _on(day: str, start: str, end: str) -> bool:
    from .dates import effective_on
    return effective_on(day, start, end)
