"""规则测试：python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gazetteer import engine, importer, intervals  # noqa: E402
from gazetteer.storage import Storage  # noqa: E402
from gazetteer.validation import validate_catalog  # noqa: E402

DEMO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo")


def _catalog(name="catalog-rev-1985.json"):
    return importer.build_catalog(importer.load_json_file(os.path.join(DEMO, name)))


def _archives():
    return [importer.normalize_archive(a)
            for a in importer.load_json_file(os.path.join(DEMO, "archives.json"))]


def _by_id(rows, key):
    return {r[key]: r for r in rows}


class TestIntervals(unittest.TestCase):
    def test_subtract_keeps_fragments(self):
        self.assertEqual(intervals.subtract([(1, 100)], (1, 50)), [(51, 100)])
        self.assertEqual(intervals.subtract([(1, 100)], (40, 60)),
                         [(1, 39), (61, 100)])
        self.assertEqual(intervals.subtract([(1, 25), (26, 50)], (1, 25)),
                         [(26, 50)])

    def test_closed_bounds(self):
        self.assertEqual(intervals.intersection((1, 50), (50, 99)), (50, 50))
        self.assertIsNone(intervals.intersection((1, 49), (50, 99)))


class TestDateSemantics(unittest.TestCase):
    def test_half_open_and_closed_house(self):
        from gazetteer.dates import effective_on
        self.assertTrue(effective_on("1980-01-01", "1949-01-01", "1985-06-01"))
        self.assertFalse(effective_on("1985-06-01", "1949-01-01", "1985-06-01"))


class TestNameResolution(unittest.TestCase):
    def setUp(self):
        self.cat = _catalog()

    def test_exact_match_renamed_street_keeps_id(self):
        ids = self.cat.street_ids_named("同文街", "1980-01-01")
        self.assertEqual(ids, ["S2", "S3"])  # 两条同名街都保留

    def test_old_name_invalid_after_rename(self):
        # 太平街 1980-01-01 起改名解放路，旧名在新日期查不到（原字匹配）
        self.assertEqual(self.cat.street_ids_named("太平街", "1981-01-01"), [])
        self.assertEqual(self.cat.street_ids_named("解放路", "1981-01-01"), ["S4"])
        # 编号沿用
        self.assertEqual(self.cat.street_ids_named("解放路", "1981-01-01"),
                         self.cat.street_ids_named("太平街", "1979-12-31"))

    def test_district_rename_original_text(self):
        self.assertEqual(self.cat.district_id_named("第二区", "1989-12-31"), "D2")
        self.assertIsNone(self.cat.district_id_named("第二区", "1990-01-01"))
        self.assertEqual(self.cat.district_id_named("城中区", "1990-01-01"), "D2")


class TestInitialCandidates(unittest.TestCase):
    def setUp(self):
        self.cat = _catalog()
        self.archives = _by_id(_archives(), "archive_id")

    def test_district_and_house_filter(self):
        r = engine.resolve_initial(self.cat, "中山路", "第一区", 10, "1980-03-01")
        self.assertIsNone(r["unmatched"])
        self.assertEqual(len(r["candidates"]), 1)
        self.assertEqual(r["candidates"][0]["intervals"], [(10, 10)])
        self.assertEqual(r["candidates"][0]["district_id"], "D1")

    def test_no_district_keeps_homonyms(self):
        r = engine.resolve_initial(self.cat, "同文街", None, None, "1980-03-03")
        self.assertEqual(len(r["candidates"]), 2)

    def test_no_house_keeps_full_range(self):
        r = engine.resolve_initial(self.cat, "中山路", None, None, "1980-03-05")
        self.assertEqual({c["intervals"][0] for c in r["candidates"]},
                         {(1, 99), (100, 200)})

    def test_homonym_with_district_picks_one(self):
        r = engine.resolve_initial(self.cat, "同文街", "第一区", 40, "1980-03-07")
        self.assertEqual(len(r["candidates"]), 1)
        self.assertEqual(r["candidates"][0]["street_id"], "S2")

    def test_unmatched_reported_separately(self):
        r = engine.resolve_initial(self.cat, "不存在的街", None, None, "1980-01-01")
        self.assertFalse(r["candidates"])
        self.assertEqual(r["unmatched"]["reason"], "street")
        r2 = engine.resolve_initial(self.cat, "太平街", None, 20, "1981-01-02")
        self.assertEqual(r2["unmatched"]["reason"], "street")  # 旧名失效


class TestTransferSimulation(unittest.TestCase):
    def setUp(self):
        self.cat = _catalog()
        self.archives = _by_id(_archives(), "archive_id")

    def q(self, aid, day):
        return engine.query_archive(self.cat, self.archives[aid], day)

    def test_a001_chain_then_partial_transfer(self):
        # 1980 落款 D1(10号)；1985-06-01 T1 划 D2；1995 T4 把 1-25 划 D4
        # （10 号随之进 D4）；2000 年 T5 是 D2→D3，10 号已不在 D2，不能再被划走
        r = self.q("A001", "2005-01-01")
        self.assertEqual(len(r["final"]), 1)
        self.assertEqual(r["final"][0]["district"]["district_id"], "D4")
        self.assertEqual(r["final"][0]["intervals"], [(10, 10)])
        evs = r["candidates"][0]["events"]
        tids = [t["transfer_id"] for ev in evs for t in ev["transfers"]]
        self.assertEqual(tids, ["T1", "T4"])

    def test_later_transfer_only_uses_remaining_part(self):
        # 40 号：1985 T1 进 D2；1995 T4 只划 1-25，40 号留在 D2；
        # 2000 T5(D2 1-50→D3) 只能继续用剩余部分，40 号划入 D3
        a = dict(self.archives["A001"])
        a["archive_id"] = "A001X"
        a["house_number"] = 40
        r = engine.query_archive(self.cat, a, "2005-01-01")
        self.assertEqual(len(r["final"]), 1)
        self.assertEqual(r["final"][0]["district"]["district_id"], "D3")
        self.assertEqual(r["final"][0]["intervals"], [(40, 40)])
        tids = [t["transfer_id"] for ev in r["candidates"][0]["events"]
                for t in ev["transfers"]]
        self.assertEqual(tids, ["T1", "T5"])
        # T4 与 40 号无关，不出现在依据里

    def test_broken_part_on_district_abolition(self):
        # S2 31-50 号在 D1 撤销时没有去向记录
        r = self.q("A003", "2005-01-01")
        unknown = {u["street"]["street_id"]: u for u in r["unknown"]}
        self.assertIn("S2", unknown)
        self.assertEqual(unknown["S2"]["broken_on"], "1985-06-01")
        self.assertEqual(unknown["S2"]["intervals"], [(31, 50)])
        self.assertEqual(unknown["S2"]["district"]["district_id"], "D1")
        # 有去向的部分仍有最终归属
        finals = {(f["street"]["street_id"], f["district"]["district_id"])
                  for f in r["final"]}
        self.assertIn(("S2", "D2"), finals)
        self.assertIn(("S3", "D4"), finals)

    def test_ambiguity_remains_visible_when_converging(self):
        r = self.q("A003", "2020-01-01")
        # T7 把 S3 1-50 也划到 D2，两个候选最终同区，但仍分两条记录
        d2 = [f for f in r["final"] if f["district"]["district_id"] == "D2"]
        self.assertEqual(len(d2), 2)
        self.assertEqual({f["street"]["street_id"] for f in d2}, {"S2", "S3"})
        for f in d2:
            self.assertEqual(len(f["from_candidates"]), 1)  # 候选不合并
        self.assertEqual(len(r["candidates"]), 2)

    def test_signing_day_transfer_not_applied(self):
        # 落款 1985-06-01，当天划转按变更前归属，10 号仍属 D1（M1）
        r = self.q("A008", "1985-06-01")
        self.assertEqual(len(r["final"]), 1)
        self.assertEqual(r["final"][0]["district"]["district_id"], "D1")
        self.assertEqual(r["candidates"][0]["events"], [])
        # 次日划转生效
        r2 = self.q("A008", "1985-06-02")
        self.assertEqual(r2["final"][0]["district"]["district_id"], "D2")

    def test_same_day_transfers_use_pre_change_snapshot(self):
        # A006 60 号：T2 同日划 D3；再用 T5（2000 年 D2 1-50→D3）不应碰 60 号
        r = self.q("A006", "2005-01-01")
        self.assertEqual(len(r["final"]), 1)
        self.assertEqual(r["final"][0]["district"]["district_id"], "D3")
        self.assertEqual(r["final"][0]["intervals"], [(60, 60)])

    def test_two_transfers_same_day_split_from_one_piece(self):
        # 无门牌的 S1 1-99 在 1985-06-01 由 T1(1-50→D2)、T2(51-99→D3)
        # 两条同日划转按变更前同一快照拆分，互不重叠、各自有依据
        a = dict(self.archives["A007"])
        a["archive_id"] = "A007X"
        r = engine.query_archive(self.cat, a, "1985-06-02")
        d1_pieces = [f for f in r["final"]
                     if f["street"]["street_id"] == "S1"
                     and f["district"]["district_id"] in ("D2", "D3")]
        by = {}
        for f in d1_pieces:
            by.setdefault(f["district"]["district_id"], []).extend(f["intervals"])
        self.assertIn((1, 50), by["D2"])       # T1 划入
        self.assertIn((100, 200), by["D2"])    # 原本就在 D2 的部分不受影响
        self.assertEqual(by["D3"], [(51, 99)])
        ev = next(e for e in r["candidates"][0]["events"]
                  if any(t["transfer_id"] in ("T1", "T2") for t in e["transfers"]))
        self.assertEqual({t["transfer_id"] for t in ev["transfers"]}, {"T1", "T2"})

    def test_membership_half_open_end_day(self):
        # M1 末日 1985-06-01（不含）：6 月 1 日靠 M1B 承接，5 月 31 日仍命中 M1
        r_old = engine.resolve_initial(self.cat, "中山路", "第一区", 10, "1985-05-31")
        self.assertEqual(r_old["candidates"][0]["membership_ids"], ["M1"])

    def test_district_rename_does_not_break_id(self):
        # D2 1990 改名城中区，编号沿用；用旧编号反查任何日期都稳定
        r = engine.reverse_lookup(self.cat, list(self.archives.values()),
                                  "D2", "1980-01-01")
        self.assertEqual(r["district_id"], "D2")
        self.assertEqual(r["district"]["name"], "第二区")  # 当日有效名

    def test_rename_displayed_by_day_name(self):
        r = self.q("A004", "2005-01-01")
        f = r["final"][0]
        self.assertEqual(f["street"]["name"], "解放路")  # 1980 改名后的当日名
        self.assertEqual(f["street"]["street_id"], "S4")  # 编号沿用
        self.assertEqual(f["district"]["name"], "城东区")  # D3 改名后名称

    def test_unmatched_archive(self):
        r = self.q("A005", "2005-01-01")
        self.assertFalse(r["resolved"])
        self.assertTrue(r["unmatched"]["detail"])
        self.assertEqual(r["final"], [])
        self.assertEqual(r["unknown"], [])

    def test_query_day_before_signing_rejected(self):
        with self.assertRaises(ValueError):
            self.q("A001", "1979-12-31")

    def test_no_house_multiple_final_districts(self):
        r = self.q("A007", "2005-01-01")
        districts = {f["district"]["district_id"] for f in r["final"]}
        self.assertEqual(districts, {"D2", "D3", "D4"})
        # S1 100-200 始终留 D2
        d2 = next(f for f in r["final"] if f["district"]["district_id"] == "D2")
        self.assertIn((100, 200), d2["intervals"])

    def test_evidence_on_each_move(self):
        r = self.q("A001", "2005-01-01")
        for ev in r["candidates"][0]["events"]:
            for mv in ev["moved"]:
                self.assertTrue(mv["transfer_ids"])


class TestReverseLookup(unittest.TestCase):
    def setUp(self):
        self.cat = _catalog()
        self.archives = _archives()

    def rev(self, district, day):
        return engine.reverse_lookup(self.cat, self.archives, district, day)

    def test_each_archive_once_with_flags(self):
        r = self.rev("D2", "2005-01-01")
        ids = [m["archive_id"] for m in r["matches"]]
        self.assertEqual(len(ids), len(set(ids)))  # 每份档案只列一次
        by = _by_id(r["matches"], "archive_id")
        self.assertTrue(by["A003"]["may_belong_elsewhere"])  # S3 在 D4
        self.assertTrue(by["A003"]["has_unknown_part"])      # S2 31-50 断档
        self.assertFalse(by["A002"]["may_belong_elsewhere"])
        self.assertFalse(by["A002"]["has_unknown_part"])
        # A001 在 2005 年已无 D2 部分（26-50→D3，1-25→D4）
        self.assertNotIn("A001", by)

    def test_reverse_by_name_and_id(self):
        by_name = self.rev("城中区", "2005-01-01")
        by_id = self.rev("D2", "2005-01-01")
        self.assertEqual([m["archive_id"] for m in by_name["matches"]],
                         [m["archive_id"] for m in by_id["matches"]])

    def test_unmatched_listed_separately(self):
        r = self.rev("D2", "2005-01-01")
        self.assertIn("A005", {u["archive_id"] for u in r["unresolved_archives"]})

    def test_archives_after_signing_only(self):
        # 落款日晚于查询日的档案不出现
        r = self.rev("D1", "1970-01-01")
        self.assertNotIn("A001", {m["archive_id"] for m in r["matches"]})


class TestValidation(unittest.TestCase):
    def test_good_catalog_passes(self):
        self.assertTrue(validate_catalog(_catalog()).ok)

    def test_conflicting_revision_rejected(self):
        report = validate_catalog(_catalog("catalog-rev-bad.json"))
        self.assertFalse(report.ok)
        self.assertEqual(len(report.conflicts), 1)
        c = report.conflicts[0]
        self.assertEqual(c["effective_on"], "1999-01-01")
        self.assertEqual(c["house_overlap"], "30-40号")
        self.assertEqual({t["to_district_id"] for t in c["transfers"]}, {"D3", "D4"})

    def test_same_target_duplicate_is_allowed(self):
        from gazetteer.models import Transfer
        cat = _catalog()
        cat.transfers.append(Transfer("TX", "1999-01-01", "S1", 1, 10, "D2", "D3"))
        cat.transfers.append(Transfer("TY", "1999-01-01", "S1", 5, 15, "D2", "D3"))
        self.assertTrue(validate_catalog(cat).ok)


class TestStorageAndCitations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self.store = Storage(self.db)
        self.cat = _catalog()
        self.bad = _catalog("catalog-rev-bad.json")
        for a in _archives():
            self.store.upsert_archive(a)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _import(self, catalog, ok_expected):
        payload = importer.load_json_file(os.path.join(
            DEMO, "catalog-rev-1985.json" if ok_expected else "catalog-rev-bad.json"))
        report = validate_catalog(catalog)
        self.store.insert_revision(
            catalog, catalog.note, importer.content_hash(payload),
            json.dumps({"ok": report.ok}), ok=report.ok)
        if report.ok:
            self.store.activate(catalog.revision_id)
        else:
            self.store.mark_rejected(catalog.revision_id)
        return report

    def test_rejection_keeps_previous_active(self):
        self._import(self.cat, True)
        self.assertEqual(self.store.active_revision_id(), "rev-1985")
        report = self._import(self.bad, False)
        self.assertFalse(report.ok)
        # 继续用上一份可用目录
        self.assertEqual(self.store.active_revision_id(), "rev-1985")
        with self.assertRaises(ValueError):
            self.store.load_catalog("rev-bad")  # 被拒修订明细未保存
        # 旧修订仍可正常加载
        self.assertEqual(self.store.load_catalog("rev-1985").revision_id, "rev-1985")

    def test_persistence_across_reopen(self):
        self._import(self.cat, True)
        ref = self.store.save_citation(
            engine.query_archive(self.cat,
                                 self.store.get_archive("A001"), "2005-01-01"))
        self.store.close()

        store2 = Storage(self.db)  # 模拟关掉工具再打开
        self.assertEqual(store2.active_revision_id(), "rev-1985")
        self.assertEqual(len(store2.all_archives()), 9)
        data = store2.get_citation(ref)
        self.assertEqual(data["revision_id"], "rev-1985")
        self.assertEqual(data["result"]["archive_id"], "A001")
        self.assertEqual(data["result"]["query_day"], "2005-01-01")
        self.assertEqual(data["result"]["address_text"], "中山路十号第一区公所函")
        store2.close()

    def test_citation_immutable_after_new_revision(self):
        self._import(self.cat, True)
        result = engine.query_archive(self.cat,
                                      self.store.get_archive("A001"), "2005-01-01")
        ref = self.store.save_citation(result)
        snapshot = self.store.get_citation(ref)["result"]

        # 编目员保存并启用一份更正后的完整目录（改备注/数据后以新编号启用）
        new_cat = importer.build_catalog({
            **importer.load_json_file(os.path.join(DEMO, "catalog-rev-1985.json")),
            "revision_id": "rev-2026",
            "note": "更正版"})
        payload = importer.load_json_file(os.path.join(DEMO, "catalog-rev-1985.json"))
        payload["revision_id"] = "rev-2026"
        self.store.insert_revision(
            new_cat, "更正版", importer.content_hash(payload),
            json.dumps({"ok": True}), ok=True)
        self.store.activate("rev-2026")
        self.assertEqual(self.store.active_revision_id(), "rev-2026")

        # 旧引用重开仍是当时的修订、日期、结果与依据
        reopened = self.store.get_citation(ref)["result"]
        self.assertEqual(reopened["revision_id"], "rev-1985")
        self.assertEqual(json.dumps(reopened, sort_keys=True),
                         json.dumps(snapshot, sort_keys=True))

    def test_duplicate_revision_id_rejected(self):
        self._import(self.cat, True)
        with self.assertRaises(ValueError):
            self.store.insert_revision(
                self.cat, "x", "h", json.dumps({}), ok=True)

    def test_reopen_unknown_ref(self):
        self.assertIsNone(self.store.get_citation("REF999999"))


if __name__ == "__main__":
    unittest.main()
