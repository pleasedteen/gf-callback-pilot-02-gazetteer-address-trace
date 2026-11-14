"""JSON 导入：把编目员准备的目录/档案 JSON 转成领域对象。

目录 JSON 结构::

    {
      "revision_id": "rev-1985",
      "note": "1985 年城区调整目录",
      "streets":   [{"id": "S1", "name": "中山路", "valid_from": "1949-01-01",
                     "valid_to": null}, ...],
      "districts": [{"id": "D1", "name": "第一区", "established_on": "1949-01-01",
                     "abolished_on": null,
                     "names": [{"name": "第一区", "valid_from": "1949-01-01",
                                "valid_to": null}]}, ...],
      "memberships": [{"id": "M1", "street_id": "S1", "district_id": "D1",
                       "lo": 1, "hi": 99, "valid_from": "1949-01-01",
                       "valid_to": null}, ...],
      "transfers": [{"id": "T1", "effective_on": "1985-06-01", "street_id": "S1",
                     "lo": 1, "hi": 50, "from_district_id": "D1",
                     "to_district_id": "D2"}, ...]
    }
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import dates
from .models import (Catalog, District, DistrictName, Membership, StreetName,
                     Transfer)


def content_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def build_catalog(payload: dict[str, Any]) -> Catalog:
    rid = payload["revision_id"]
    catalog = Catalog(revision_id=rid, note=payload.get("note", ""))

    for s in payload.get("streets", []):
        vf, vt = dates.valid_range(s["valid_from"], s.get("valid_to"))
        catalog.streets.append(StreetName(s["id"], s["name"], vf, vt))

    for d in payload.get("districts", []):
        catalog.district_life.append(District(
            d["id"], dates.parse(d["established_on"]).isoformat(),
            dates.parse(d["abolished_on"]).isoformat()
            if d.get("abolished_on") else None))
        names = d.get("names")
        if not names:
            names = [{"name": d["name"], "valid_from": d["established_on"],
                      "valid_to": d.get("abolished_on")}]
        for n in names:
            vf, vt = dates.valid_range(n["valid_from"], n.get("valid_to"))
            catalog.districts.append(DistrictName(d["id"], n["name"], vf, vt))

    for m in payload.get("memberships", []):
        vf, vt = dates.valid_range(m["valid_from"], m.get("valid_to"))
        catalog.memberships.append(Membership(
            m["id"], m["street_id"], m["district_id"],
            int(m["lo"]), int(m["hi"]), vf, vt))

    for t in payload.get("transfers", []):
        catalog.transfers.append(Transfer(
            t["id"], dates.parse(t["effective_on"]).isoformat(),
            t["street_id"], int(t["lo"]), int(t["hi"]),
            t["from_district_id"], t["to_district_id"]))

    return catalog


def normalize_archive(a: dict[str, Any]) -> dict[str, Any]:
    required = ["archive_id", "signing_date", "address_text", "street_name"]
    missing = [k for k in required if not a.get(k)]
    if missing:
        raise ValueError(f"档案缺少必填字段: {', '.join(missing)}")
    dates.parse(a["signing_date"])
    house = a.get("house_number")
    return {
        "archive_id": a["archive_id"],
        "signing_date": dates.parse(a["signing_date"]).isoformat(),
        "address_text": a["address_text"],
        "street_name": a["street_name"],
        "district_name": a.get("district_name") or None,
        "house_number": int(house) if house is not None else None,
    }


def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
