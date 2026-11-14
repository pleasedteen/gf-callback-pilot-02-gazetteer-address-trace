"""日期工具：统一 ISO 字符串 (YYYY-MM-DD)，区间一律含首日、不含末日 [start, end)。"""
from __future__ import annotations

from datetime import date

INF = "9999-12-31"  # 仍有效的名称/归属用远末日表示开区间


def parse(s: str) -> date:
    if not isinstance(s, str):
        raise ValueError(f"日期必须是 YYYY-MM-DD 字符串: {s!r}")
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def to_str(d: date) -> str:
    return d.isoformat()


def valid_range(start: str, end: str | None) -> tuple[str, str]:
    """返回规整后的 [start, end)；end 为空表示至今。"""
    s = parse(start).isoformat()
    if end is None or end == "":
        return s, INF
    e = parse(end).isoformat()
    if not e > s:
        raise ValueError(f"日期区间必须含首日不含末日，末日须晚于首日: {start} ~ {end}")
    return s, e


def effective_on(day: str, start: str, end: str) -> bool:
    """day 是否落在 [start, end) 内。"""
    d = parse(day)
    return parse(start) <= d < parse(end)


def as_orderable(day: str):
    return parse(day)
