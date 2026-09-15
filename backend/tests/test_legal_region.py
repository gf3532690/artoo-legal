"""地域抽取（``pipeline/legal_region.py``）单测。

钉住三件事：省级/直辖市/市级的优先级、从正文批准机关推省份的边界（不能被日期或引用
污染）、以及由语料推导的映射表兜底。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import pytest

from app.pipeline.legal_region import (
    city_at_start,
    city_province_map,
    county_at_start,
    find_approving_province,
    resolve_region,
)


class TestFindApprovingProvince:
    def test_finds_province_in_approval_clause(self) -> None:
        head = (
            "菏泽市煤炭清洁生产使用监督管理条例\n"
            "（2016年12月23日菏泽市第十八届人民代表大会常务委员会第三十七次会议通过  "
            "2017年1月18日山东省第十二届人民代表大会常务委员会第二十五次会议批准）"
        )

        assert find_approving_province(head) == "山东省"

    def test_date_prefix_does_not_leak_into_province_name(self) -> None:
        """「2021年5月26日吉林省…」——按已知省名匹配，不能抓出「日吉林省」。"""
        head = "（2021年5月26日吉林省第十三届人民代表大会常务委员会第二十七次会议批准）"

        assert find_approving_province(head) == "吉林省"

    def test_mentioned_province_without_approval_is_not_trusted(self) -> None:
        head = "第二条　本条例与河北省的地方性法规不一致时，适用本条例。" * 20

        assert find_approving_province(head) is None

    def test_longest_province_name_wins(self) -> None:
        head = "（2019年11月29日内蒙古自治区第十三届人民代表大会常务委员会第十六次会议批准）"

        assert find_approving_province(head) == "内蒙古自治区"

    def test_empty_text(self) -> None:
        assert find_approving_province("") is None


class TestResolveRegion:
    def test_province_level_regulation(self) -> None:
        assert resolve_region("河北省土壤污染防治条例", "河北省人民代表大会常务委员会") == (
            "河北省", None
        )

    def test_municipality_sets_province_and_city(self) -> None:
        assert resolve_region("北京市物业管理条例", "北京市人民代表大会常务委员会") == (
            "北京市", "北京市"
        )

    def test_city_level_regulation_uses_approval_clause(self) -> None:
        head = "（2017年1月18日山东省第十二届人民代表大会常务委员会第二十五次会议批准）"

        assert resolve_region("菏泽市煤炭清洁生产使用监督管理条例", None, head) == (
            "山东省", "菏泽市"
        )

    def test_city_level_regulation_falls_back_to_map(self) -> None:
        """批准机关取不到时用推导出的城市表兜底。"""
        assert resolve_region("深圳市城市轨道交通条例", None, None) == ("广东省", "深圳市")

    def test_unknown_province_keeps_city_only(self) -> None:
        """城市能从法名推出来就留着，省份推不出就留空——不猜。"""
        assert resolve_region("某虚构市条例", None, None) == (None, "某虚构市")

    def test_county_level_keeps_province_only(self) -> None:
        head = "（2016年12月23日广西壮族自治区人民代表大会常务委员会第二十五次会议批准）"

        assert resolve_region("三江侗族自治县自治条例", "三江侗族自治县人民代表大会", head) == (
            "广西壮族自治区", None
        )

    def test_county_name_helper(self) -> None:
        assert county_at_start("三江侗族自治县人民代表大会") == "三江侗族自治县"
        assert county_at_start("河北省人民代表大会常务委员会") is None


class TestCityBoundary:
    """城市名边界：既不能把后面的词吃进来，也不能把真城市名挡掉。

    实测事故：``^([\\u4e00-\\u9fa5]{2,10}?)(?:市|自治州|地区|盟)`` 配 ``+ "市"`` 会把
    「阜新蒙古族自治县县城市容…」抽成 33 字节的「阜新蒙古族自治县县城市」，写 Milvus 时
    撑爆 varchar(32)；同时把「德宏傣族景颇族自治州」拼成「德宏傣族景颇族市」。
    """

    @pytest.mark.parametrize("text,expected", [
        ("厦门市禁止燃放烟花爆竹规定", "厦门市"),
        ("深圳市城市轨道交通条例", "深圳市"),
        # 「…城市」：真名 stem 都是 2 字
        ("景德镇市城市管理条例", "景德镇市"),
        ("塔城市城市市容和环境卫生管理条例", "塔城市"),
        ("宣城市城市绿化条例", "宣城市"),
        ("晋城市大气污染防治条例", "晋城市"),
        # 自治州 / 地区 / 盟要保留后缀，不再拼成「…市」
        ("德宏傣族景颇族自治州傣医药条例", "德宏傣族景颇族自治州"),
        ("克孜勒苏柯尔克孜自治州条例", "克孜勒苏柯尔克孜自治州"),
        ("阿勒泰地区条例", "阿勒泰地区"),
        ("锡林郭勒盟工作委员会工作条例", "锡林郭勒盟"),
    ])
    def test_keeps_the_real_city(self, text: str, expected: str) -> None:
        assert city_at_start(text) == expected

    @pytest.mark.parametrize("text", [
        # 前缀把后面的词吃进来（曾经的「…县县城市」「…城镇市」）
        "阜新蒙古族自治县县城市容和环境卫生管理条例",
        "河南蒙古族自治县城镇市容市貌管护条例",
        "门源回族自治县城镇市容和环境卫生管理条例",
        "彭水苗族土家族自治县市容和环境卫生管理条例",
        # 「城市」这个词被当前缀
        "中华人民共和国城市维护建设税法",
        "北京城市副中心条例",
        "城市市容和环境卫生管理条例",
        # 「市」属于普通词
        "人力资源市场暂行条例",
        "乐东黎族自治县旅游市场管理条例",
        # 「地区」是普通名词，不是行政区（只认封闭清单）
        "中国公民往来台湾地区管理办法",
        "吉林省西部地区生态环境保护与建设若干规定",
        "广东省促进民族地区发展条例",
        # 省级前缀由 resolve_region 的省级分支处理，这里不猜
        "山西省不设区的市和市辖区人民代表大会常务委员会街道工作委员会工作条例",
        "广东省粤港澳大湾区内地九市轨道交通发展条例",
        "北京市物业管理条例",
    ])
    def test_rejects_over_capture(self, text: str) -> None:
        assert city_at_start(text) is None

    def test_province_prefix_still_yields_city(self) -> None:
        """省名之后直接跟州/市名时不能丢城市，否则「按城市筛选」永远查不到。"""
        assert resolve_region("云南省德宏傣族景颇族自治州傣医药条例", None, None) == (
            "云南省", "德宏傣族景颇族自治州"
        )

    def test_province_level_regulation_keeps_city_empty(self) -> None:
        assert resolve_region("河北省土壤污染防治条例", None, None) == ("河北省", None)


class TestCityProvinceMapData:
    """映射表是从语料推导并随仓库提交的数据，用它当回归护栏。"""

    def test_committed_map_covers_major_cities(self) -> None:
        mapping = city_province_map()

        assert mapping.get("深圳市") == "广东省"
        assert mapping.get("厦门市") == "福建省"
        assert mapping.get("长春市") == "吉林省"
        assert mapping.get("菏泽市") == "山东省"

    def test_map_values_are_known_provinces(self) -> None:
        from app.pipeline.legal_region import PROVINCES

        mapping = city_province_map()

        assert mapping
        assert set(mapping.values()) <= set(PROVINCES)

    def test_map_keeps_suffixes_and_has_no_over_capture(self) -> None:
        """表里不能有被拼坏的假名，且后缀要保留（自治州不再变成「…族市」）。"""
        mapping = city_province_map()

        assert "景德镇市" in mapping
        assert "大理白族自治州" in mapping
        assert "德宏傣族景颇族自治州" in mapping
        assert not [name for name in mapping if "族市" in name or "自治县" in name]

    def test_map_names_fit_the_milvus_field(self) -> None:
        """字段上限按语料里最长的真实地名定（自治州 33 字节 / 县级 45 字节），取 64。"""
        from app.storage.milvus import LEGAL_FILTER_FIELD_LENGTHS

        mapping = city_province_map()
        longest = max(len(name.encode("utf-8")) for name in mapping)

        assert longest > 32, "语料里真实地名已超过旧的 32 字节上限"
        assert longest <= LEGAL_FILTER_FIELD_LENGTHS["city"]
