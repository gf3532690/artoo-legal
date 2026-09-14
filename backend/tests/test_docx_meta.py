"""docx 内嵌属性读取（``pipeline/docx_meta.py``）单测。

用 ``zipfile`` 现造合成 docx，不依赖真实语料——这里要钉死的是读取层的边界行为：
缺部件、非 zip、非 ``lpwstr`` 值、非 ISO 日期、部件损坏，都必须降级而不是抛异常。
"""

from __future__ import annotations

# 导入 app.pipeline.* 会连带触发 app.config.get_settings()，缺少 JWT_SECRET 时
# fail-fast；新克隆没有 backend/.env，因此这里在导入前补一份仅测试用的默认值。
import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import zipfile
from pathlib import Path

from app.pipeline.docx_meta import extract_docx_props

_CORE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:coreProperties'
    ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/">{body}</cp:coreProperties>'
)

_CUSTOM_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:Properties'
    ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    "{body}</cp:Properties>"
)


def _core(**fields: str) -> str:
    """按 core.xml 的镜像约定生成部件内容。"""
    tags = {
        "title": "dc:title",
        "subject": "dc:subject",
        "creator": "dc:creator",
        "description": "dc:description",
    }
    body = "".join(f"<{tags[k]}>{v}</{tags[k]}>" for k, v in fields.items())
    return _CORE_XML.format(body=body)


def _custom(**fields: str) -> str:
    """按 custom.xml 的形态生成部件内容，值一律用 ``vt:lpwstr``。"""
    body = "".join(
        f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{i + 2}"'
        f' name="{name}"><vt:lpwstr>{value}</vt:lpwstr></property>'
        for i, (name, value) in enumerate(fields.items())
    )
    return _CUSTOM_XML.format(body=body)


def _write_docx(path: Path, *, core: str | None = None, custom: str | None = None) -> Path:
    """写一个只含 docProps 的最小 docx（zip）。"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if core is not None:
            archive.writestr("docProps/core.xml", core)
        if custom is not None:
            archive.writestr("docProps/custom.xml", custom)
    return path


class TestExtractDocxProps:
    def test_reads_known_fields_from_custom_properties(self, tmp_path: Path) -> None:
        path = _write_docx(
            tmp_path / "a.docx",
            core=_core(title="河北省土壤污染防治条例"),
            custom=_custom(
                title="河北省土壤污染防治条例",
                authority="河北省人民代表大会常务委员会",
                law_type="地方法规",
                publish_date="2021-11-23",
                effective_date="2022-01-01",
                validity_status="3",
                external_id="ff8081817e00906b017e055ae8c00e16",
                source_code="national_laws",
            ),
        )

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.title == "河北省土壤污染防治条例"
        assert props.authority == "河北省人民代表大会常务委员会"
        assert props.law_type == "地方法规"
        assert props.publish_date == "2021-11-23"
        assert props.effective_date == "2022-01-01"
        assert props.validity_status == 3
        assert props.external_id == "ff8081817e00906b017e055ae8c00e16"
        assert props.source_code == "national_laws"
        assert props.origin == "custom+core"

    def test_core_mirror_is_used_when_custom_is_absent(self, tmp_path: Path) -> None:
        """第三方 Word 文档可能只有 core.xml，镜像字段要能兜底。"""
        path = _write_docx(
            tmp_path / "b.docx",
            core=_core(
                title="中华人民共和国民法典",
                subject="法律",
                creator="全国人民代表大会常务委员会",
                description="publish_date=2020-05-28; effective_date=2021-01-01",
            ),
        )

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.title == "中华人民共和国民法典"
        assert props.authority == "全国人民代表大会常务委员会"
        assert props.law_type == "法律"
        assert props.publish_date == "2020-05-28"
        assert props.effective_date == "2021-01-01"
        assert props.origin == "core"

    def test_custom_wins_over_core(self, tmp_path: Path) -> None:
        path = _write_docx(
            tmp_path / "c.docx",
            core=_core(title="旧名", creator="旧机关", description="publish_date=2000-01-01"),
            custom=_custom(title="新名", authority="新机关", publish_date="2021-11-23"),
        )

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.title == "新名"
        assert props.authority == "新机关"
        assert props.publish_date == "2021-11-23"

    def test_docx_without_docprops_returns_none(self, tmp_path: Path) -> None:
        path = _write_docx(tmp_path / "d.docx")

        assert extract_docx_props(str(path)) is None

    def test_non_zip_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "not_a_docx.docx"
        path.write_text("这不是一个 zip", encoding="utf-8")

        assert extract_docx_props(str(path)) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert extract_docx_props(str(tmp_path / "nowhere.docx")) is None

    def test_non_lpwstr_value_types_are_read(self, tmp_path: Path) -> None:
        """只认 ``vt:lpwstr`` 会让 ``vt:i4`` / ``vt:filetime`` 字段静默变空。"""
        custom = _CUSTOM_XML.format(
            body=(
                '<property name="validity_status"><vt:i4>3</vt:i4></property>'
                '<property name="title"><vt:lpwstr>某条例</vt:lpwstr></property>'
            )
        )
        path = _write_docx(tmp_path / "e.docx", custom=custom)

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.validity_status == 3
        assert props.title == "某条例"

    def test_empty_value_element_is_skipped(self, tmp_path: Path) -> None:
        custom = _CUSTOM_XML.format(
            body=(
                '<property name="title"><vt:lpwstr/></property>'
                '<property name="authority"><vt:lpwstr>某机关</vt:lpwstr></property>'
            )
        )
        path = _write_docx(tmp_path / "f.docx", custom=custom)

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.title is None
        assert props.authority == "某机关"

    def test_non_iso_date_is_not_trusted(self, tmp_path: Path) -> None:
        """日期不是 ISO 就不采信——宁可留空回退正文解析，也不猜。"""
        path = _write_docx(
            tmp_path / "g.docx",
            custom=_custom(publish_date="2021年11月23日", effective_date="2022/01/01"),
        )

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.publish_date is None
        assert props.effective_date is None

    def test_unknown_keys_are_kept_in_extra(self, tmp_path: Path) -> None:
        """上游加字段不应导致读取层改代码。"""
        path = _write_docx(
            tmp_path / "h.docx",
            custom=_custom(title="某条例", future_field="v1"),
        )

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.extra == {"future_field": "v1"}

    def test_broken_custom_part_keeps_core_values(self, tmp_path: Path) -> None:
        """单部件损坏只丢该部件，不能连带丢弃另一个。"""
        path = _write_docx(
            tmp_path / "i.docx",
            core=_core(title="某条例", creator="某机关"),
            custom="<cp:Properties><未闭合>",
        )

        props = extract_docx_props(str(path))

        assert props is not None
        assert props.title == "某条例"
        assert props.authority == "某机关"
        assert props.origin == "core"
