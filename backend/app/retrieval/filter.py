"""检索过滤条件模块

提供 RetrievalFilter dataclass，用于构造 Milvus pre-filter 表达式：按 doc_id、
file_type，以及法条专属的**效力层级**与**地域**组合过滤。

两点设计取舍：

- **层级用枚举、不用语料原始类别**：对外只暴露 ``LEGAL_LEVEL_TYPES`` 里的层级名，
  原始 ``law_type`` 取值（12 个，含"修改、废止的决定"这类语料口径）留在应用层映射，
  这样口径变化不需要重建索引。
- **地域过滤保留国家层面法规**：指定省市时，``province == ""``（法律 / 行政法规 /
  司法解释等无地域归属的文档）始终保留，命中的地方性法规才是被"筛"的对象。否则
  一个"物业费 + 北京市"的查询会把《民法典》之类完全排除，与 PRD 的意图相反。
"""

from __future__ import annotations

from dataclasses import dataclass

# 对外暴露的效力层级 → 语料 ``law_type`` 取值集合。
#
# 口径说明：
# - ``law`` 只含国家法律层级的法条本身与法律解释/修正案；
# - ``decision`` 单列：修改、废止的决定既可能是国家层面（全国人大常委会）也可能是
#   地方层面（省市人大），语料只给了类别、没给层级，因此不强行归入 ``law``；
# - ``supervision_regulation``（监察法规）按《立法法》单列，不并入行政法规。
LEGAL_LEVEL_TYPES: dict[str, tuple[str, ...]] = {
    "constitution": ("宪法",),
    "law": (
        "法律",
        "法律解释",
        "修正案",
        "法规性决定",
        "有关法律问题和重大问题的决定（部分）",
    ),
    "decision": ("修改、废止的决定",),
    "administrative_regulation": ("行政法规",),
    "judicial_interpretation": ("司法解释",),
    "local_regulation": ("地方法规",),
    "supervision_regulation": ("监察法规",),
}


def law_types_for_levels(levels: list[str] | None) -> list[str]:
    """把层级名展开成语料 ``law_type`` 取值列表；未知层级直接忽略（不报错）。

    服务端不因为一个拼错的层级名就让整次检索失败——与"取不到的过滤字段不命中"一致。
    """
    if not levels:
        return []
    types: list[str] = []
    for level in levels:
        types.extend(LEGAL_LEVEL_TYPES.get(level, ()))
    return list(dict.fromkeys(types))


def _quote(value: str) -> str:
    """Milvus 字符串字面量转义。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass
class RetrievalFilter:
    """检索过滤条件"""

    doc_ids: list[str] | None = None  # 限定文档范围
    file_types: list[str] | None = None  # 限定文件类型
    law_types: list[str] | None = None  # 限定语料 law_type（通常由层级展开而来）
    province: str | None = None  # 限定省份；国家层面法规（province 为空）始终保留
    city: str | None = None  # 限定城市；国家层面法规（province 为空）始终保留

    def to_milvus_expr(self) -> str | None:
        """转换为 Milvus filter 表达式

        Returns:
            合法的 Milvus expr 字符串，多条件用 " and " 连接；
            无过滤条件时返回 None。
        """
        parts = []
        if self.doc_ids:
            ids_str = ", ".join(_quote(d) for d in self.doc_ids)
            parts.append(f"doc_id in [{ids_str}]")
        if self.file_types:
            types_str = ", ".join(_quote(t) for t in self.file_types)
            parts.append(f"file_type in [{types_str}]")
        if self.law_types:
            types_str = ", ".join(_quote(t) for t in self.law_types)
            parts.append(f"law_type in [{types_str}]")

        # 地域：国家层面法规（province 为空串）永远保留；指定省市时只筛地方性法规。
        if self.province and self.city:
            parts.append(
                f"(province == \"\" or (province == {_quote(self.province)}"
                f" and (city == \"\" or city == {_quote(self.city)})))"
            )
        elif self.province:
            parts.append(f"(province == \"\" or province == {_quote(self.province)})")
        elif self.city:
            parts.append(f"(province == \"\" or city == {_quote(self.city)})")
        return " and ".join(parts) if parts else None
