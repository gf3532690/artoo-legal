"""法条地域（省 / 市级）抽取。

PRD 要求按省份/城市筛选地方性法规，而语料里没有结构化地域字段。实测可抽性
（地方法规 25,285 份，抽样 3,000 份统计）：

- 49.8%：``issuing_authority`` 或 ``law_name`` 直接以省 / 自治区 / 直辖市开头；
- 44.6%：只以市名开头，但正文头部的批准机关里带省份
  （如「2017年1月18日山东省第十二届人民代表大会常务委员会…批准」）——**城市到省份
  的关系可以从业内语料自己推出来**，不需要手写一张城市表；
- 5.6%：头部也推不出（经济特区类文书、批准机关被截断等）。

省份来源优先级：① 自带省名 → ② 正文批准机关 → ③ 城市→省份映射表
（由 :mod:`app.scripts.build_city_province_map` 从语料生成，落在
``pipeline/city_province.json``，与模块同目录）。三级都取不到就留空——地域筛选对空值表现为
"不命中"，不猜。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# 34 个省级行政区。用**已知名单**匹配而不是正则猜前缀：正文里的
# 「2021年5月26日吉林省…」会让「取后缀为省的最短中文字串」这类正则抓到「日吉林省」。
PROVINCES: tuple[str, ...] = (
    "北京市", "天津市", "上海市", "重庆市",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "海南省",
    "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省",
    "台湾省", "内蒙古自治区", "广西壮族自治区", "西藏自治区",
    "宁夏回族自治区", "新疆维吾尔自治区", "香港特别行政区", "澳门特别行政区",
)

_MUNICIPALITIES = ("北京市", "天津市", "上海市", "重庆市")

_PROVINCE_AT_START = re.compile(
    r"^(?:" + "|".join(sorted(PROVINCES, key=len, reverse=True)) + r")"
)
_CITY_AT_START = re.compile(r"^([\u4e00-\u9fa5]{2,10}?)(?:市|自治州|地区|盟)")
_COUNTY_AT_START = re.compile(r"^([\u4e00-\u9fa5]{2,12}?(?:县|自治县|旗|自治旗))")

# 正文里出现省级机关的最长跨度（批准 / 审议 / 人大常委会 等表述都在其后不远处）。
_APPROVAL_WINDOW = 60

_DATA_FILE = Path(__file__).with_name("city_province.json")


@lru_cache(maxsize=1)
def city_province_map() -> dict[str, str]:
    """城市 → 省份映射（由语料推导，见 ``scripts/build_city_province_map.py``）。"""
    try:
        with open(_DATA_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def _province_at_start(text: str) -> str | None:
    """文本是否以省级行政区开头。"""
    match = _PROVINCE_AT_START.match((text or "").strip())
    return match.group(0) if match else None


def find_approving_province(text: str) -> str | None:
    """在正文里找省级批准/审议机关，返回省份名。

    只在候选省名之后 ``_APPROVAL_WINDOW`` 个字符内出现「批准 / 审议 / 通过 /
    人大常委会 / 人民代表大会」时才采信，避免把正文里引用的其它省份当成批准机关。
    """
    if not text:
        return None
    for province in sorted(PROVINCES, key=len, reverse=True):
        start = 0
        while True:
            index = text.find(province, start)
            if index < 0:
                break
            tail = text[index + len(province): index + len(province) + _APPROVAL_WINDOW]
            if any(token in tail for token in ("批准", "审议", "通过", "人大", "人民政府")):
                return province
            start = index + len(province)
    return None


def resolve_region(
    law_name: str | None = None,
    issuing_authority: str | None = None,
    head_text: str | None = None,
) -> tuple[str | None, str | None]:
    """解析 ``(province, city)``；取不到的位置为 ``None``。

    Args:
        law_name: 法名（docx 属性优先）。
        issuing_authority: 发布机关（docx 属性优先）。
        head_text: 正文头部文本（用于从批准机关推省份），可省略。

    Returns:
        ``(province, city)``。直辖市按「province == city == 直辖市名」返回；县级只回填
        省份（省份同样可能来自批准机关或映射表），城市留空。
    """
    for source in (issuing_authority, law_name):
        text = (source or "").strip()
        if not text:
            continue
        province = _province_at_start(text)
        if province:
            # 直辖市：province 与 city 同名；「北京市朝阳区…」这类仍按直辖市返回。
            if province in _MUNICIPALITIES:
                return province, province
            return province, None

    city: str | None = None
    for source in (issuing_authority, law_name):
        text = (source or "").strip()
        if not text:
            continue
        match = _CITY_AT_START.match(text)
        if match:
            city = match.group(1) + "市"
            break

    # 省份：正文批准机关优先，其次城市映射表。
    province = find_approving_province(head_text or "")
    if not province and city:
        province = city_province_map().get(city)
        # 「深圳经济特区…」这类法名不以市名开头，机关名里带市名时映射表同样适用。
        if not province:
            for candidate, mapped in city_province_map().items():
                if candidate and candidate in (issuing_authority or ""):
                    province = mapped
                    city = city or candidate
                    break
    return province, city


def county_at_start(text: str | None) -> str | None:
    """县级行政区名（自治县 / 县 / 旗）；用于覆盖统计，不参与检索过滤。"""
    match = _COUNTY_AT_START.match((text or "").strip())
    return match.group(1) if match else None
