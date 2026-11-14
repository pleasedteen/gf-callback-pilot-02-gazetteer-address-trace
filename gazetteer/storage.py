"""SQLite 持久化：档案、目录修订、引用编号。"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any

from .models import (Catalog, District, DistrictName, Membership, StreetName,
                     Transfer)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS archives(
    archive_id    TEXT PRIMARY KEY,
    signing_date  TEXT NOT NULL,
    address_text  TEXT NOT NULL,
    street_name   TEXT NOT NULL,
    district_name TEXT,
    house_number  INTEGER
);

CREATE TABLE IF NOT EXISTS revisions(
    revision_id       TEXT PRIMARY KEY,
    note              TEXT,
    status            TEXT NOT NULL,        -- draft / active / rejected / superseded
    imported_at       TEXT NOT NULL,
    content_hash      TEXT,
    validation_report TEXT
);

CREATE TABLE IF NOT EXISTS street_names(
    revision_id TEXT NOT NULL, street_id TEXT NOT NULL, name TEXT NOT NULL,
    valid_from TEXT NOT NULL, valid_to TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS district_names(
    revision_id TEXT NOT NULL, district_id TEXT NOT NULL, name TEXT NOT NULL,
    valid_from TEXT NOT NULL, valid_to TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS district_life(
    revision_id TEXT NOT NULL, district_id TEXT NOT NULL,
    established_on TEXT NOT NULL, abolished_on TEXT);
CREATE TABLE IF NOT EXISTS memberships(
    revision_id TEXT NOT NULL, membership_id TEXT NOT NULL, street_id TEXT NOT NULL,
    district_id TEXT NOT NULL, lo INTEGER NOT NULL, hi INTEGER NOT NULL,
    valid_from TEXT NOT NULL, valid_to TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transfers(
    revision_id TEXT NOT NULL, transfer_id TEXT NOT NULL, effective_on TEXT NOT NULL,
    street_id TEXT NOT NULL, lo INTEGER NOT NULL, hi INTEGER NOT NULL,
    from_district_id TEXT NOT NULL, to_district_id TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS citations(
    ref_id      TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    archive_id  TEXT NOT NULL,
    query_day   TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""

DEFAULT_DB = os.path.join(os.path.expanduser("~"), ".gazetteer", "gazetteer.db")


class Storage:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("GAZETTEER_DB") or DEFAULT_DB
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -------------------------------------------------------- meta
    def _meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _meta_put(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # -------------------------------------------------------- 档案
    def upsert_archive(self, a: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO archives(archive_id, signing_date, address_text, "
            "street_name, district_name, house_number) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(archive_id) DO UPDATE SET signing_date=excluded.signing_date, "
            "address_text=excluded.address_text, street_name=excluded.street_name, "
            "district_name=excluded.district_name, house_number=excluded.house_number",
            (a["archive_id"], a["signing_date"], a["address_text"],
             a["street_name"], a.get("district_name"), a.get("house_number")))
        self.conn.commit()

    def get_archive(self, archive_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM archives WHERE archive_id=?",
                                (archive_id,)).fetchone()
        return dict(row) if row else None

    def all_archives(self) -> list[dict[str, Any]]:
        return [dict(r) for r in
                self.conn.execute("SELECT * FROM archives ORDER BY archive_id")]

    # -------------------------------------------------------- 修订
    def revision_exists(self, revision_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM revisions WHERE revision_id=?",
                                 (revision_id,)).fetchone() is not None

    def list_revisions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT revision_id, note, status, imported_at, content_hash "
            "FROM revisions ORDER BY imported_at").fetchall()
        return [dict(r) for r in rows]

    def active_revision_id(self) -> str | None:
        return self._meta_get("active_revision")

    def insert_revision(self, catalog: Catalog, note: str,
                        content_hash: str, report_json: str, ok: bool) -> None:
        """原子写入整份修订。ok=False 时只留 rejected 元数据，不写明细。"""
        rid = catalog.revision_id
        if self.revision_exists(rid):
            raise ValueError(f"修订编号 {rid} 已存在，请更换编号")
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO revisions(revision_id, note, status, imported_at, "
                    "content_hash, validation_report) VALUES(?,?,?,?,?,?)",
                    (rid, note, "draft", now, content_hash, report_json))
                if ok:
                    self._bulk_insert(catalog)
        except Exception:
            raise

    def _bulk_insert(self, catalog: Catalog) -> None:
        rid = catalog.revision_id
        self.conn.executemany(
            "INSERT INTO street_names VALUES(?,?,?,?,?)",
            [(rid, s.street_id, s.name, s.valid_from, s.valid_to) for s in catalog.streets])
        self.conn.executemany(
            "INSERT INTO district_names VALUES(?,?,?,?,?)",
            [(rid, d.district_id, d.name, d.valid_from, d.valid_to)
             for d in catalog.districts])
        self.conn.executemany(
            "INSERT INTO district_life VALUES(?,?,?,?)",
            [(rid, d.district_id, d.established_on, d.abolished_on)
             for d in catalog.district_life])
        self.conn.executemany(
            "INSERT INTO memberships VALUES(?,?,?,?,?,?,?,?)",
            [(rid, m.membership_id, m.street_id, m.district_id, m.lo, m.hi,
              m.valid_from, m.valid_to) for m in catalog.memberships])
        self.conn.executemany(
            "INSERT INTO transfers VALUES(?,?,?,?,?,?,?,?)",
            [(rid, t.transfer_id, t.effective_on, t.street_id, t.lo, t.hi,
              t.from_district_id, t.to_district_id) for t in catalog.transfers])

    def activate(self, revision_id: str) -> str | None:
        """启用指定修订；把原启用修订置为 superseded。返回原启用编号（可能为 None）。"""
        row = self.conn.execute("SELECT status FROM revisions WHERE revision_id=?",
                                (revision_id,)).fetchone()
        if row is None:
            raise ValueError(f"修订 {revision_id} 不存在")
        if row["status"] == "rejected":
            raise ValueError(f"修订 {revision_id} 未通过校验，不能启用")
        old = self.active_revision_id()
        with self.conn:
            if old:
                self.conn.execute(
                    "UPDATE revisions SET status='superseded' WHERE revision_id=?", (old,))
            self.conn.execute(
                "UPDATE revisions SET status='active' WHERE revision_id=?", (revision_id,))
            self._meta_put("active_revision", revision_id)
        return old

    def mark_rejected(self, revision_id: str) -> None:
        self.conn.execute("UPDATE revisions SET status='rejected' WHERE revision_id=?",
                          (revision_id,))
        self.conn.commit()

    def load_catalog(self, revision_id: str) -> Catalog:
        row = self.conn.execute("SELECT * FROM revisions WHERE revision_id=?",
                                (revision_id,)).fetchone()
        if row is None:
            raise ValueError(f"修订 {revision_id} 不存在")
        if row["status"] == "rejected":
            raise ValueError(f"修订 {revision_id} 是被拒绝启用的修订，明细未保存")
        c = Catalog(revision_id=revision_id, note=row["note"] or "")
        c.streets = [StreetName(*r) for r in self.conn.execute(
            "SELECT street_id, name, valid_from, valid_to FROM street_names "
            "WHERE revision_id=?", (revision_id,))]
        c.districts = [DistrictName(*r) for r in self.conn.execute(
            "SELECT district_id, name, valid_from, valid_to FROM district_names "
            "WHERE revision_id=?", (revision_id,))]
        c.district_life = [District(r["district_id"], r["established_on"],
                                    r["abolished_on"]) for r in self.conn.execute(
            "SELECT district_id, established_on, abolished_on FROM district_life "
            "WHERE revision_id=?", (revision_id,))]
        c.memberships = [Membership(*r) for r in self.conn.execute(
            "SELECT membership_id, street_id, district_id, lo, hi, valid_from, valid_to "
            "FROM memberships WHERE revision_id=?", (revision_id,))]
        c.transfers = [Transfer(*r) for r in self.conn.execute(
            "SELECT transfer_id, effective_on, street_id, lo, hi, "
            "from_district_id, to_district_id FROM transfers WHERE revision_id=?",
            (revision_id,))]
        return c

    # -------------------------------------------------------- 引用
    def save_citation(self, result: dict[str, Any]) -> str:
        n = int(self._meta_get("next_ref_seq") or "0") + 1
        ref_id = f"REF{n:06d}"
        self._meta_put("next_ref_seq", str(n))
        with self.conn:
            self.conn.execute(
                "INSERT INTO citations(ref_id, created_at, revision_id, archive_id, "
                "query_day, result_json) VALUES(?,?,?,?,?,?)",
                (ref_id, datetime.now().isoformat(timespec="seconds"),
                 result["revision_id"], result["archive_id"], result["query_day"],
                 json.dumps(result, ensure_ascii=False, sort_keys=True)))
        return ref_id

    def get_citation(self, ref_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM citations WHERE ref_id=?",
                                (ref_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["result"] = json.loads(data.pop("result_json"))
        return data

    def list_citations(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT ref_id, created_at, revision_id, archive_id, query_day "
            "FROM citations ORDER BY ref_id")]
