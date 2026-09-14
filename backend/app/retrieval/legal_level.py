"""法条效力层级的词汇表：过滤用的层级枚举 + 排序用的位阶阶梯。

两个用途共用一处定义，避免口径分叉：

- **过滤**：``LEGAL_LEVEL_TYPES`` 把对外层级名映射成语料 ``law_type`` 取值，
  供 ``RetrievalFilter`` 拼 Milvus ``expr``；
- **排序**：``LEGAL_LEVEL_TIERS`` 给每个 ``law_type`` 一个 [0,1] 的位阶分，
  供检索链路的复合评分做"高层级优先"。

两处都建立在**语料原始类别**之上，而对外只暴露层级枚举——口径变化只需改这里，
不需要重建索引（Milvus 里存的是原始 ``law_type``）。
"""

from __future__ import annotations

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

# 位阶阶梯（越接近 1 越高）。用于排序加权，不用于过滤。
LEGAL_LEVEL_TIERS: dict[str, float] = {
    "宪法": 1.0,
    "法律": 0.9,
    "法律解释": 0.9,
    "修正案": 0.9,
    "法规性决定": 0.85,
    "有关法律问题和重大问题的决定（部分）": 0.85,
    "修改、废止的决定": 0.85,  # 国家层面的废止决定；地方层面用下面的 _LOCAL_TIER 覆盖
    "行政法规": 0.8,
    "监察法规": 0.75,
    "司法解释": 0.7,
    "地方法规": 0.6,
}

# 「修改、废止的决定」在两级的位阶不同：全国人大常委会的废止决定属法律层级，
# 省市人大的属地方性法规层级。语料只有类别、没有级别，这里用 ``province`` 区分。
_DECISION_LOCAL_TIER = 0.6


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


def level_tier(law_type: str | None, province: str | None = None) -> float | None:
    """取位阶分；无法判定时返回 ``None``（调用方据此跳过加权）。

    非法条语料（Artoo 的普通知识库）没有 ``law_type``，因此天然不加权——这让本机制
    在两个产品线共用的检索链路上零副作用。
    """
    if not law_type:
        return None
    if law_type == "修改、废止的决定" and (province or "").strip():
        return _DECISION_LOCAL_TIER
    return LEGAL_LEVEL_TIERS.get(law_type)
