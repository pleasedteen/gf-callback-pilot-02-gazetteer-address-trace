"""命令行入口。

用法示例：
  python -m gazetteer.cli import-catalog rev1.json
  python -m gazetteer.cli import-archives archives.json
  python -m gazetteer.cli query A001                       # 今天 / 最新启用目录
  python -m gazetteer.cli query A001 --on 2000-01-01 --revision rev-1985 --cite
  python -m gazetteer.cli reverse D2 --on 2000-01-01
  python -m gazetteer.cli reopen REF000001
"""
from __future__ import annotations

import argparse
import json
import sys

from . import engine, importer, rendering
from .dates import parse as parse_date
from .storage import Storage
from .validation import validate_catalog


def _load_catalog(store: Storage, revision: str | None):
    rid = revision or store.active_revision_id()
    if not rid:
        raise SystemExit("尚无启用的目录修订，请先 import-catalog 一份通过校验的目录。")
    return store.load_catalog(rid), rid


def cmd_import_catalog(store: Storage, args) -> int:
    payload = importer.load_json_file(args.file)
    catalog = importer.build_catalog(payload)
    digest = importer.content_hash(payload)
    report = validate_catalog(catalog)
    report_json = json.dumps(
        {"ok": report.ok, "conflicts": report.conflicts, "errors": report.errors},
        ensure_ascii=False, indent=2)

    if not report.ok:
        store.insert_revision(catalog, catalog.note, digest, report_json, ok=False)
        store.mark_rejected(catalog.revision_id)
        print(f"修订 {catalog.revision_id} 校验未通过，已拒绝启用，继续使用上一份可用目录。")
        for e in report.errors:
            print("  完整性错误：" + e)
        for c in report.conflicts:
            tids = " 与 ".join(t["transfer_id"] for t in c["transfers"])
            targets = "、".join(t["to_district_id"] for t in c["transfers"])
            print(f"  冲突：{c['effective_on']} 旧区 {c['from_district_id']} "
                  f"街 {c['street_id']} 门牌 {c['house_overlap']} "
                  f"被 {tids} 划给不同区（{targets}）")
        print(f"\n拒绝记录已留存（状态 rejected），明细未写入：{catalog.revision_id}")
        return 2

    store.insert_revision(catalog, catalog.note, digest, report_json, ok=True)
    old = store.activate(catalog.revision_id)
    if old:
        print(f"修订 {catalog.revision_id} 校验通过并已启用；上一份修订 {old} 置为 superseded。")
    else:
        print(f"修订 {catalog.revision_id} 校验通过并已启用（首份目录）。")
    print(f"  街名 {len(catalog.streets)} 条，区名 {len(catalog.districts)} 条，"
          f"归属 {len(catalog.memberships)} 段，划转 {len(catalog.transfers)} 条")
    return 0


def cmd_import_archives(store: Storage, args) -> int:
    payload = importer.load_json_file(args.file)
    if isinstance(payload, dict):
        payload = payload.get("archives", [])
    n = 0
    for raw in payload:
        store.upsert_archive(importer.normalize_archive(raw))
        n += 1
    print(f"已保存/更新档案 {n} 份（重复编号按最新内容覆盖）。")
    return 0


def _resolve_day(arg_day: str | None) -> str:
    return parse_date(arg_day).isoformat() if arg_day else _today()


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def cmd_query(store: Storage, args) -> int:
    archive = store.get_archive(args.archive_id)
    if archive is None:
        raise SystemExit(f"档案 {args.archive_id} 不存在。")
    catalog, rid = _load_catalog(store, args.revision)
    day = _resolve_day(args.on)
    if parse_date(day) < parse_date(archive["signing_date"]):
        raise SystemExit(f"查询日期 {day} 早于落款日 {archive['signing_date']}，"
                         "指定日期须在落款当天或之后。")
    result = engine.query_archive(catalog, archive, day)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(rendering.render_query(result))

    if args.cite:
        ref = store.save_citation(result)
        print(f"\n已生成可重开编目引用：{ref}（绑定修订 {rid}、查询日期 {day}）")
    return 0


def cmd_reverse(store: Storage, args) -> int:
    catalog, _ = _load_catalog(store, args.revision)
    day = _resolve_day(args.on)
    try:
        result = engine.reverse_lookup(catalog, store.all_archives(), args.district, day)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(rendering.render_reverse(result))
    return 0


def cmd_reopen(store: Storage, args) -> int:
    data = store.get_citation(args.ref_id)
    if data is None:
        raise SystemExit(f"引用 {args.ref_id} 不存在。")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"引用编号：{data['ref_id']}    生成时间：{data['created_at']}")
    print("=" * 64)
    print(rendering.render_query(data["result"]))
    print("=" * 64)
    print("说明：引用重放的是生成时保存的结果快照（含档案原文、目录修订、查询日期、"
          "结果与依据），后续目录修订不影响本引用。")
    return 0


def cmd_list(store: Storage, args) -> int:
    if args.kind == "revisions":
        print(rendering.render_revisions(store.list_revisions(),
                                         store.active_revision_id()))
    elif args.kind == "archives":
        rows = store.all_archives()
        if not rows:
            print("（暂无档案）")
        for a in rows:
            extra = []
            if a["district_name"]:
                extra.append(a["district_name"])
            if a["house_number"] is not None:
                extra.append(f"{a['house_number']}号")
            tail = ("（" + "，".join(extra) + "）") if extra else ""
            print(f"  {a['archive_id']}  落款 {a['signing_date']}  "
                  f"{a['street_name']}{tail}  《{a['address_text']}》")
    else:
        rows = store.list_citations()
        if not rows:
            print("（暂无引用）")
        for r in rows:
            print(f"  {r['ref_id']}  档案 {r['archive_id']}  查询日 {r['query_day']}  "
                  f"修订 {r['revision_id']}  生成于 {r['created_at']}")
    return 0


def cmd_use(store: Storage, args) -> int:
    try:
        old = store.activate(args.revision_id)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(f"已启用修订 {args.revision_id}。" +
          (f"原启用修订 {old} 置为 superseded。" if old else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gazetteer",
                                description="地方志编目本地查档工具")
    p.add_argument("--db", help="SQLite 数据库路径（默认 ~/.gazetteer/gazetteer.db，"
                                "也可用环境变量 GAZETTEER_DB）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("import-catalog", help="导入并尝试启用一份完整目录修订（JSON）")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_import_catalog)

    sp = sub.add_parser("import-archives", help="批量导入/更新档案（JSON 数组）")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_import_archives)

    sp = sub.add_parser("query", help="按档案编号查询今天或指定日期的归属")
    sp.add_argument("archive_id")
    sp.add_argument("--on", dest="on", help="查询日期 YYYY-MM-DD，默认今天")
    sp.add_argument("--revision", help="指定目录修订，默认最新启用")
    sp.add_argument("--cite", action="store_true", help="同时生成可重开的编目引用编号")
    sp.add_argument("--json", action="store_true", help="输出 JSON")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("reverse", help="从一个区反查某天可能归属它的档案")
    sp.add_argument("district", help="区编号或当日有效区名（原字匹配）")
    sp.add_argument("--on", dest="on", help="查询日期 YYYY-MM-DD，默认今天")
    sp.add_argument("--revision", help="指定目录修订，默认最新启用")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reverse)

    sp = sub.add_parser("reopen", help="用引用编号重开历史查询")
    sp.add_argument("ref_id")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_reopen)

    sp = sub.add_parser("list", help="列出 revisions / archives / citations")
    sp.add_argument("kind", choices=["revisions", "archives", "citations"])
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("use", help="启用一份已保存且通过校验的旧修订")
    sp.add_argument("revision_id")
    sp.set_defaults(func=cmd_use)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Storage(args.db)
    try:
        return args.func(store, args)
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
