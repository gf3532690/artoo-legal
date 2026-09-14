"""地域抽取（``pipeline/legal_region.py``）单测。

钉住三件事：省级/直辖市/市级的优先级、从正文批准机关推省份的边界（不能被日期或引用
污染）、以及由语料推导的映射表兜底。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from app.pipeline.legal_region import (
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
