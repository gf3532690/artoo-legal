"""法条文本的共享词法常量与中文数字转换。

本模块是「条号 / 中文数字」相关规则的**单一事实源**，供法律文书切分器
(``pipeline/chunkers/laws.py``) 与法条元数据抽取器
(``pipeline/legal_metadata.py``) 共用。

背景：改造前仓库里有四处各自为政的中文数字正则（``chunkers/laws.py``、
``chunker.py`` 两处、``metadata.py``），但它们**只做匹配、不做转换**——
没有任何一处能把「第一百四十六条」变成整数 146。所以这里既收敛常量，
也补上缺失的转换函数。
"""

from __future__ import annotations

import re

# 中文数字字符集。含「零〇两」，覆盖「第一百零一条」「两」这类写法。
CN_NUMERAL_CHARS = "零〇一二三四五六七八九十百千万两"

# 行首「第X条」——注意用 MULTILINE 锚定行首：正文里出现的「第X条」
# （如修正案条目中的引用）不是结构标记，不能参与切分。
ARTICLE_LINE_PATTERN = re.compile(
    rf"^[^\S\n]*第([{CN_NUMERAL_CHARS}\d]+)条",
    re.MULTILINE,
)

# 行首「一、」式条目：修正案 / 决定 / 规定类文档的一级结构单元。
ITEM_LINE_PATTERN = re.compile(
    rf"^[^\S\n]*([{CN_NUMERAL_CHARS}]+)、",
    re.MULTILINE,
)

# 出现在正文任意位置的「第X条」引用（不锚定行首）。
ARTICLE_REFERENCE_PATTERN = re.compile(rf"第[{CN_NUMERAL_CHARS}\d]+条")

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def chinese_to_int(text: str) -> int | None:
    """把中文数字串转成整数；无法解析时返回 ``None``。

    支持「一百四十六」「二十四」「十五」「一千零一」「三百八十四」「一万」等
    常见写法，以及纯阿拉伯数字串。**不**支持大写「壹贰叁」与「廿」「卅」等
    生僻写法——样本语料中未出现，遇到时返回 ``None``，由上层按「字段缺失」
    处理而不是猜一个值。

    Args:
        text: 待转换的字符串，可含前后空白。

    Returns:
        整数值；空串、含未知字符、或超出可解析范围时返回 ``None``。
    """
    s = (text or "").strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except ValueError:  # pragma: no cover - isdigit 已排除
            return None

    total = 0
    section = 0
    number = 0
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
                number = 0
            else:
                # 「十五」的十位省略了「一」，按惯例补 1。
                if number == 0:
                    number = 1
                section += number * unit
                number = 0
        else:
            return None
    return total + section + number
