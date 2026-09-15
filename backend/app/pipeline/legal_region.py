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

# 市级/州级后缀。长后缀排在前面：前缀用非贪婪量词，必须让它先撞上最长的后缀，
# 否则「德宏傣族景颇族自治州」会在「…族」之后先匹配出别的东西。
_CITY_SUFFIXES = ("自治州", "地区", "盟", "市")

# 名字部分（后缀之前）以这些字结尾，说明前缀把后面的词吃进来了，不是一个地名：
#   …自治县 + 市 →「彭水苗族土家族自治县市」；…城市 + 市 →「城市市」。这类值会把
#   varchar 字段撑爆。（「镇」不在此列：景德镇市 / 丰镇市 是真的，见下面的「城镇」判据。）
_CITY_BAD_NAME_TAIL = frozenset("县区市")

# 「地区」与「盟」现存只剩 10 个（地区 7 + 盟 3），而这两个后缀无法靠字符判断边界——
# 「中国公民往来台湾地区」「西部地区」「南疆地区」和「阿勒泰地区」长得一模一样。所以它们
# 只在下面这个封闭清单里认，不参与猜测。
_PREFECTURES = frozenset({
    "阿勒泰地区", "塔城地区", "阿克苏地区", "喀什地区", "和田地区", "阿里地区",
    "大兴安岭地区", "锡林郭勒盟", "阿拉善盟", "兴安盟",
})

# 「市」后面紧跟这些字时，它属于普通词而不是地名边界：市场 / 市容 / 市民 / 市区。
_CITY_BAD_NEXT_AFTER_SHI = frozenset("场容民区")

_CITY_AT_START = re.compile(
    r"^([\u4e00-\u9fa5]{2,12}?(?:" + "|".join(_CITY_SUFFIXES) + r"))"
)
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


def city_at_start(text: str | None) -> str | None:
    """从文本开头取市级/州级行政区名（**含后缀**，如「厦门市」「德宏傣族景颇族自治州」）。

    取不到返回 ``None``。

    为什么一条正则解决不了：地名的边界不能只看字符判断——「人力资源市场条例」前四个字后面
    也是「市」，「阜新蒙古族自治县县城市容…」也是，「中华人民共和国城市维护建设税法」也是。
    所以这里在正则之上加两道边界判据，把实测到的假样本挡掉：

    - 名字不能以另一个行政区后缀结尾（``_CITY_BAD_NAME_TAIL``）；
    - 「市」后面不能紧跟市场/市容/市民/市区这类词的下一字（``_CITY_BAD_NEXT_AFTER_SHI``）。

    真正的主力是 :func:`resolve_region` 里的映射表白名单（表里的名字就是正确边界），本函数
    只负责白名单还没覆盖到的新城市。
    """
    text = (text or "").strip()
    match = _CITY_AT_START.match(text)
    if not match:
        return None
    name = match.group(1)
    # 带省级前缀的文本由 resolve_region 的省级分支先行处理（剥掉省名后只认白名单），
    # 这里不再猜——否则「山西省不设区的市和市辖区…条例」会被切成「山西省不设区的市」。
    if _province_at_start(name):
        return None
    suffix = next(s for s in _CITY_SUFFIXES if name.endswith(s))
    stem = name[: -len(suffix)]
    if not stem or stem[-1] in _CITY_BAD_NAME_TAIL:
        return None
    # 「城镇」是词不是地名后缀：河南蒙古族自治县城镇市 这种要挡掉，同时放过 景德镇市 / 丰镇市。
    if stem.endswith("城镇"):
        return None
    # 「地区」「盟」只认封闭清单（见 _PREFECTURES 的说明）。
    if suffix in ("地区", "盟") and name not in _PREFECTURES:
        return None
    # 「城」单独处理：塔城市 / 宣城市 / 晋城市 是真的（stem 都是 2 字），而把「城市」这个词
    # 当前缀吃掉的那些假名（中华人民共和国城市、阜新蒙古族自治县县城市、双鸭山城市）stem
    # 都 ≥3 字。实测语料里合法的「…城市」stem 全是 2 字，这条能干净分开。
    if stem.endswith("城") and len(stem) >= 3:
        return None
    if suffix == "市" and text[match.end():match.end() + 1] in _CITY_BAD_NEXT_AFTER_SHI:
        return None
    return name


def _city_from_map(text: str) -> str | None:
    """按映射表做最长前缀匹配。表是随仓库提交的白名单，优先级高于正则。"""
    best: str | None = None
    for name in city_province_map():
        if text.startswith(name) and (best is None or len(name) > len(best)):
            best = name
    return best


def city_of_title(text: str | None) -> str | None:
    """从法名 / 发布机关名里取城市（州）名：先查白名单，查不到才用正则猜。

    :func:`resolve_region` 的普通分支与 ``scripts/build_city_province_map`` 共用它——两边必须是
    同一套「城市从哪里开始、到哪里结束」的规则，否则推导出来的表和运行时抽取会互相打架
    （这正是 ``临夏回族市`` 这类假名同时进表和进索引的原因）。
    """
    text = (text or "").strip()
    if not text:
        return None
    return _city_from_map(text) or city_at_start(text)


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

    城市名的取法：**先查映射表白名单（最长前缀），查不到才用 :func:`city_at_start` 猜**。
    表里的名字是语料推导并经校验的正确边界，因此白名单能挡住「人力资源市」「城市市」这类
    正则无法自证的假名；先白名单后正则也让既有城市的取值稳定，不随正则微调漂移。
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
            # 省名之后可能直接跟州/市名：《云南省德宏傣族景颇族自治州傣医药条例》的主体是
            # 地级而非省级，丢掉城市会让「按城市筛选」永远查不到它。取不到才留空，因此
            # 省级法规（「河北省土壤污染防治条例」）的行为不变。
            #
            # 这条路**只认白名单、不跑正则**：剥掉省名之后剩下的往往不再是完整法名，正则
            # 在这里会猜出一堆非地名的东西（实测「安徽省城乡集市贸易市场管理条例」→
            # 「城乡集市」、「山西省不设区的市和市辖区…条例」→「不设区的市」）。白名单
            # 能救回 164 份（大理白族自治州这类），一次噪声都不引入。
            return province, _city_from_map(text[len(province):])

    city: str | None = None
    for source in (issuing_authority, law_name):
        text = (source or "").strip()
        if not text:
            continue
        city = city_of_title(text)
        if city:
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
