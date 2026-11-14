"""CLI 文本渲染（中文、可重开引用按同一格式重放）。"""
from __future__ import annotations

from . import intervals


def _street_label(s: dict) -> str:
    mark = "（查询日无有效名称，显示历史名）" if not s.get("name_current_on_day", True) else ""
    return f"{s['name']}[{s['street_id']}]{mark}"


def _district_label(d: dict) -> str:
    mark = "（查询日无有效名称，显示历史名）" if not d.get("name_current_on_day", True) else ""
    return f"{d['name']}[{d['district_id']}]{mark}"


def render_query(result: dict) -> str:
    L: list[str] = []
    L.append(f"档案编号：{result['archive_id']}")
    L.append(f"档案原文：{result['address_text']}")
    L.append(f"落款日期：{result['signing_day']}    查询日期：{result['query_day']}")
    L.append(f"目录修订：{result['revision_id']}")
    rec = result["recorded"]
    parts = [f"街名「{rec['street_name']}」"]
    if rec["district_name"]:
        parts.append(f"区名「{rec['district_name']}」")
    if rec["house_number"] is not None:
        parts.append(f"门牌 {rec['house_number']} 号")
    L.append("编目录入：" + "，".join(parts))
    L.append("")

    if not result["resolved"]:
        u = result["unmatched"]
        L.append("【原地址查不到】" + u["detail"])
        L.append("说明：该档案地址未在本修订目录中找到有效归属，未参与任何划转推断。")
        return "\n".join(L)

    L.append("一、落款当日候选（按原字匹配当日有效名称）")
    for c in result["candidates"]:
        L.append(
            f"  候选{c['index']}：{_street_label(c['street_at_signing'])} "
            f"{intervals.fmt(c['initial_intervals'])} → "
            f"{_district_label(c['district_at_signing'])}"
            f"（归属依据 {', '.join(c['membership_ids'])}）")
    if len(result["candidates"]) > 1:
        L.append("  注：存在多条同名候选，以下演变按候选分别保留，歧义不合并。")
    L.append("")

    L.append("二、划转演变（落款日之后；同日划转按变更前归属一起算）")
    any_event = False
    for c in result["candidates"]:
        if not c["events"]:
            continue
        any_event = True
        L.append(f"  候选{c['index']}：")
        for ev in c["events"]:
            if ev["transfers"]:
                tdesc = "；".join(
                    f"{t['transfer_id']}:{t['lo']}-{t['hi']}号 "
                    f"{t['from_district_id']}→{t['to_district_id']}"
                    for t in ev["transfers"])
                L.append(f"    {ev['on']} 划转 {tdesc}")
                for mv in ev["moved"]:
                    L.append(f"      划入 {mv['to_district_id']}："
                             f"{intervals.fmt(mv['intervals'])}"
                             f"（依据 {', '.join(mv['transfer_ids'])}）")
                if ev["remaining_after"]:
                    L.append(f"      留原区：{intervals.fmt(ev['remaining_after'])}")
            if ev["broken"]:
                L.append(f"    {ev['broken']['on']} 原区 "
                         f"{ev['broken']['district_id']} 撤销，"
                         f"{intervals.fmt(ev['remaining_after'])} 无去向记录")
    if not any_event:
        L.append("  （落款日之后无适用划转）")
    L.append("")

    L.append("三、查询日最终归属")
    if result["final"]:
        for f in result["final"]:
            src = "、".join(f"候选{i}" for i in f["from_candidates"])
            multi = "（多条历史候选归并到同一区，原歧义仍可追溯）" \
                if len(f["from_candidates"]) > 1 else ""
            L.append(f"  {_street_label(f['street'])} {intervals.fmt(f['intervals'])} "
                     f"→ {_district_label(f['district'])}（来自{src}）{multi}")
    else:
        L.append("  （无仍有有效归属的部分）")
    L.append("")

    L.append("四、去向不明部分")
    if result["unknown"]:
        for u in result["unknown"]:
            src = "、".join(f"候选{i}" for i in u["from_candidates"])
            L.append(f"  {_street_label(u['street'])} {intervals.fmt(u['intervals'])}："
                     f"断在 {u['broken_on']}，原区 "
                     f"{u['district']['name']}[{u['district']['district_id']}] 撤销"
                     f"（来自{src}）")
    else:
        L.append("  无")
    return "\n".join(L)


def render_reverse(result: dict) -> str:
    L = [f"反查区：{_district_label(result['district'])}    日期：{result['day']}",
         f"目录修订：{result['revision_id']}    命中档案数：{result['count']}",
         "（每份档案只列一次）", ""]
    if not result["matches"]:
        L.append("无可能归属该区的档案。")
    for m in result["matches"]:
        flags = []
        if m["may_belong_elsewhere"]:
            flags.append("还可能属于 " + "、".join(m["other_district_ids"]))
        if m["has_unknown_part"]:
            flags.append("含去向不明部分")
        flag_txt = "；标记：" + "，".join(flags) if flags else "；标记：无"
        L.append(f"  {m['archive_id']} 《{m['address_text']}》"
                 f" 匹配门牌 {intervals.fmt(m['matched_intervals'])}{flag_txt}")
    if result["unresolved_archives"]:
        L.append("")
        L.append("另有原地址查不到、未计入反查的档案：")
        for u in result["unresolved_archives"]:
            L.append(f"  {u['archive_id']}：{u['detail']}")
    return "\n".join(L)


def render_revisions(rows: list[dict], active: str | None) -> str:
    L = [f"当前启用：{active or '（无）'}", ""]
    for r in rows:
        star = " *启用*" if r["revision_id"] == active else ""
        L.append(f"  {r['revision_id']} [{r['status']}]{star}  {r['imported_at']}  "
                 f"{r.get('note') or ''}")
    return "\n".join(L)
