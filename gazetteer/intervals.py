"""门牌区间（含两端的整数闭区间）工具。

区间在内部统一表示为 ``(lo, hi)``，且 ``lo <= hi``。
一个地址片段可包含多个互不重叠的区间。
"""
from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

Interval = Tuple[int, int]


def make(lo: int, hi: int) -> Interval:
    if not isinstance(lo, int) or not isinstance(hi, int):
        raise ValueError(f"门牌必须是整数: {(lo, hi)!r}")
    if lo > hi:
        raise ValueError(f"门牌区间起点不能大于终点: {(lo, hi)!r}")
    return (lo, hi)


def overlap(a: Interval, b: Interval) -> bool:
    return not (a[1] < b[0] or b[1] < a[0])


def intersection(a: Interval, b: Interval) -> Interval | None:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    if lo <= hi:
        return (lo, hi)
    return None


def subtract(intervals: Iterable[Interval], cut: Interval) -> List[Interval]:
    """从一组区间中挖掉 cut 覆盖的部分，保持片段各自独立（不合并）。"""
    out: List[Interval] = []
    for iv in intervals:
        if not overlap(iv, cut):
            out.append(iv)
            continue
        if iv[0] < cut[0]:
            out.append((iv[0], cut[0] - 1))
        if cut[1] < iv[1]:
            out.append((cut[1] + 1, iv[1]))
    return out


def intersect_set(intervals: Iterable[Interval], other: Interval) -> List[Interval]:
    """一组区间与单个区间取交，保留片段粒度（不合并）。"""
    out: List[Interval] = []
    for iv in intervals:
        hit = intersection(iv, other)
        if hit:
            out.append(hit)
    return out


def total_count(intervals: Iterable[Interval]) -> int:
    return sum(hi - lo + 1 for lo, hi in intervals)


def normalize(intervals: Iterable[Interval]) -> List[Interval]:
    """规整：排序并合并相邻/重叠区间（仅用于展示或校验比较）。"""
    ordered = sorted(intervals)
    merged: List[Interval] = []
    for lo, hi in ordered:
        if not merged or lo > merged[-1][1] + 1:
            merged.append((lo, hi))
        elif hi > merged[-1][1]:
            merged[-1] = (merged[-1][0], hi)
    return merged


def fmt(intervals: Sequence[Interval]) -> str:
    if not intervals:
        return "（无门牌）"
    parts = []
    for lo, hi in intervals:
        parts.append(str(lo) if lo == hi else f"{lo}-{hi}号")
    return "、".join(parts)
