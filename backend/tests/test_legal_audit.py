"""法条语料核对脚本（``app/scripts/audit_legal_metadata.py``）单测。

钉住三件容易悄悄错的事：zip 条目名还原、逐份抽取结果、同法名多版本的选版规则。
选版规则是阶段三的输入，错一位就会淘汰掉不该淘汰的版本。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import csv
import io
import json
import zipfile
from pathlib import Path

from app.scripts.audit_legal_metadata import (
    REVIEW_NO_STATUS_3,
    REVIEW_SOLO_INACTIVE,
    REVIEW_TIED_DATE,
    DocRecord,
    audit_fast,
    decode_zip_name,
    iter_corpus,
    select_versions,
    summarize,
    write_ingest_list,
    write_reports,
)
from app.pipeline.loaders.docx_xml_text import paragraphs_from_bytes

_CORE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<cp:coreProperties'
    ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<dc:title>{title}</dc:title><dc:creator>{authority}</dc:creator></cp:coreProperties>'
)


def _custom(**fields: str) -> str:
    body = "".join(
        f'<property name="{name}"><vt:lpwstr>{value}</vt:lpwstr></property>'
        for name, value in fields.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:Properties'
        ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        f"{body}</cp:Properties>"
    )


def _document(*paragraphs: str) -> str:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


def write_docx(path: Path, *, title: str, authority: str = "某机关", **props: str) -> Path:
    """写一份带 docProps 与正文的最小 docx。"""
    document = _document(
        f"{title}",
        "（2020年5月28日第十三届全国人民代表大会第三次会议通过）",
        "第一条　为了保护民事主体的合法权益，制定本法。",
        "第二条　民法调整平等主体的自然人、法人和非法人组织之间的人身关系。",
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("docProps/core.xml", _CORE.format(title=title, authority=authority))
        archive.writestr("docProps/custom.xml", _custom(title=title, authority=authority, **props))
        archive.writestr("word/document.xml", document)
    return path


class TestDecodeZipName:
    def test_restores_utf8_bytes_decoded_as_cp437(self) -> None:
        """macOS 打包的 zip 未置 UTF-8 标志位，条目名是 cp437 解码结果。"""
        mojibake = "河北省土壤污染防治条例.docx".encode("utf-8").decode("cp437")

        assert decode_zip_name(mojibake, 0) == "河北省土壤污染防治条例.docx"

    def test_leaves_flagged_names_alone(self) -> None:
        assert decode_zip_name("中华人民共和国民法典.docx", 0x800) == "中华人民共和国民法典.docx"

    def test_leaves_ascii_names_alone(self) -> None:
        assert decode_zip_name("plain.docx", 0) == "plain.docx"

    def test_keeps_name_when_restore_is_not_utf8(self) -> None:
        """还原失败宁可留乱码，也不能改成另一个错名字。"""
        assert decode_zip_name("café.docx", 0) == "café.docx"


class TestIterCorpus:
    def test_directory_yields_sorted_docx_entries(self, tmp_path: Path) -> None:
        write_docx(tmp_path / "b.docx", title="乙法")
        write_docx(tmp_path / "a.docx", title="甲法")
        (tmp_path / "notes.txt").write_text("忽略", encoding="utf-8")

        assert [name for name, _ in iter_corpus(str(tmp_path))] == ["a.docx", "b.docx"]

    def test_sampling_is_deterministic(self, tmp_path: Path) -> None:
        """抽样要可复现，否则容量推算无法比对两次运行。"""
        for index in range(6):
            write_docx(tmp_path / f"f{index}.docx", title=f"第{index}法")

        first = [name for name, _ in iter_corpus(str(tmp_path), sample=3, seed=7)]
        second = [name for name, _ in iter_corpus(str(tmp_path), sample=3, seed=7)]

        assert len(first) == 3
        assert first == second
        assert first == sorted(first)

    def test_sample_larger_than_corpus_returns_everything(self, tmp_path: Path) -> None:
        write_docx(tmp_path / "a.docx", title="甲法")

        assert len(list(iter_corpus(str(tmp_path), sample=10, seed=1))) == 1


class TestDocumentParagraphs:
    """段落抽取已收敛到共享实现（loader 的兜底与核对脚本共用一份）。

    fixture 必须用真实的 WordprocessingML 命名空间：抽取按命名空间 URI 识别 ``w:p`` /
    ``w:t``（前缀可以任意），不再是按 ``<w:p>`` 字面量做字符串匹配。
    """

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def _docx_bytes(self, document_xml: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    def test_extracts_paragraph_text(self) -> None:
        data = self._docx_bytes(
            f'<?xml version="1.0"?><w:document xmlns:w="{self._W_NS}"><w:body>'
            "<w:p><w:r><w:t>第一条　正文</w:t></w:r></w:p>"
            "<w:p/>"
            "<w:p><w:r><w:t>第二条</w:t></w:r><w:r><w:t>　正文</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )

        assert paragraphs_from_bytes(data) == ["第一条　正文", "第二条　正文"]

    def test_does_not_leak_xml_markup(self) -> None:
        """``<w:tbl>`` / ``<w:tab/>`` 等以 t 开头的标签不能被当成文本标签。"""
        data = self._docx_bytes(
            f'<?xml version="1.0"?><w:document xmlns:w="{self._W_NS}"><w:body>'
            "<w:p><w:pPr><w:tabs><w:tab w:val=\"left\"/></w:tabs>"
            "<w:autoSpaceDE/><w:bidi w:val=\"0\"/></w:pPr>"
            "<w:r><w:t>国务院关于修改某条例的决定</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>（2003年7月15日国务院令第384号公布）</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )

        paragraphs = paragraphs_from_bytes(data)

        assert paragraphs == [
            "国务院关于修改某条例的决定",
            "（2003年7月15日国务院令第384号公布）",
        ]
        assert not any("<w:" in text for text in paragraphs)

    def test_table_paragraphs_are_included_in_order(self) -> None:
        """``DocxLoader`` 现在会读表格内段落，fast 模式必须对齐（否则整篇法条漏统计）。"""
        data = self._docx_bytes(
            f'<?xml version="1.0"?><w:document xmlns:w="{self._W_NS}"><w:body>'
            "<w:p><w:r><w:t>第一条　正文</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表内文字</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "<w:p><w:r><w:t>第二条　正文</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )

        assert paragraphs_from_bytes(data) == ["第一条　正文", "表内文字", "第二条　正文"]


class TestAuditFast:
    def test_extracts_props_and_counts_articles(self, tmp_path: Path) -> None:
        path = write_docx(
            tmp_path / "a.docx",
            title="中华人民共和国民法典",
            law_type="法律",
            publish_date="2020-05-28",
            effective_date="2021-01-01",
            validity_status="3",
            external_id="ext-1",
            source_code="national_laws",
        )

        record = audit_fast("a.docx", path.read_bytes())

        assert record.error == ""
        assert record.law_name == "中华人民共和国民法典"
        assert record.law_type == "法律"
        assert record.validity_status == 3
        assert record.external_id == "ext-1"
        assert record.source_code == "national_laws"
        assert record.effective_date == "2021-01-01"
        assert record.meta_source == "docx-props"
        assert record.article_count == 2
        assert record.props_origin == "custom+core"

    def test_region_is_resolved_for_regulation(self, tmp_path: Path) -> None:
        """核对报告要带地域——PRD 的地域过滤与入库清单都依赖它。"""
        path = write_docx(
            tmp_path / "c.docx",
            title="菏泽市煤炭清洁生产使用监督管理条例",
            authority="菏泽市人民代表大会常务委员会",
            law_type="地方法规",
        )

        record = audit_fast("c.docx", path.read_bytes())

        assert record.city == "菏泽市"

    def test_reports_error_instead_of_raising(self, tmp_path: Path) -> None:
        record = audit_fast("broken.docx", b"not a zip")

        assert record.error
        assert record.law_name is None


class TestSelectVersions:
    def _record(self, name: str, law: str, publish: str | None, status: int | None) -> DocRecord:
        return DocRecord(filename=name, law_name=law, publish_date=publish, validity_status=status)

    def test_prefers_active_version_then_newest(self) -> None:
        old = self._record("old.docx", "某条例", "2015-09-23", 2)
        new = self._record("new.docx", "某条例", "2021-07-30", 3)

        select_versions([old, new])

        assert new.selected is True and old.selected is False
        assert new.review_reason == ""
        assert old.group_size == new.group_size == 2

    def test_newest_wins_within_active_versions(self) -> None:
        older_active = self._record("a.docx", "某条例", "2019-11-29", 3)
        newer_active = self._record("b.docx", "某条例", "2021-01-22", 3)

        select_versions([older_active, newer_active])

        assert newer_active.selected is True and older_active.selected is False

    def test_group_without_active_version_goes_to_review(self) -> None:
        first = self._record("a.docx", "某条例", "2015-09-23", 2)
        second = self._record("b.docx", "某条例", "2019-11-29", 2)

        select_versions([first, second])

        assert second.selected is True
        assert second.review_reason == REVIEW_NO_STATUS_3

    def test_tied_publish_date_goes_to_review(self) -> None:
        first = self._record("a.docx", "某条例", "2021-07-30", 3)
        second = self._record("b.docx", "某条例", "2021-07-30", 3)

        select_versions([first, second])

        assert first.review_reason == REVIEW_TIED_DATE

    def test_solo_inactive_document_is_flagged_not_dropped(self) -> None:
        """废止决定是孤本且 status=0：正文本身有用，只标复核不淘汰。"""
        only = self._record("repeal.docx", "关于废止某条例的决定", "2021-03-31", 0)

        select_versions([only])

        assert only.selected is True
        assert only.review_reason == REVIEW_SOLO_INACTIVE

    def test_grouping_ignores_whitespace_in_law_name(self) -> None:
        spaced = self._record("a.docx", "某 条例", "2015-09-23", 2)
        tight = self._record("b.docx", "某条例", "2021-07-30", 3)

        select_versions([spaced, tight])

        assert tight.group_size == 2 and tight.selected is True


class TestReports:
    def test_summary_and_csv_files(self, tmp_path: Path) -> None:
        records = [
            DocRecord(filename="new.docx", law_name="某条例", publish_date="2021-07-30",
                      validity_status=3, article_count=10, law_type="地方法规"),
            DocRecord(filename="old.docx", law_name="某条例", publish_date="2015-09-23",
                      validity_status=2, article_count=8, law_type="地方法规"),
            DocRecord(filename="solo.docx", law_name="关于废止某条例的决定",
                      publish_date="2021-03-31", validity_status=0, article_count=0),
        ]
        select_versions(records)
        summary = summarize(records, corpus="memory", mode="fast")
        out_dir = tmp_path / "out"

        write_reports(records, summary, str(out_dir))

        assert summary["documents"] == 3
        assert summary["version_selection"]["multi_version_groups"] == 1
        assert summary["version_selection"]["documents_dropped"] == 1
        assert summary["version_selection"]["articles_kept"] == 10
        assert summary["articles"]["total"] == 18

        with open(out_dir / "files.csv", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 3
        assert rows[0]["law_name"] == "某条例"

        with open(out_dir / "manual_review.csv", encoding="utf-8-sig", newline="") as handle:
            review = list(csv.DictReader(handle))
        assert [row["reason"] for row in review] == [REVIEW_SOLO_INACTIVE]

        with open(out_dir / "summary.json", encoding="utf-8") as handle:
            assert json.load(handle)["mode"] == "fast"

    def test_summary_reports_the_single_chunk_per_parent_variant(self) -> None:
        """容量对比要用同一个口径：父块不超 child_size 就算一个子块，超了按比例切。"""
        records = [
            DocRecord(filename="a.docx", law_name="甲法", chunks=10, parents=4,
                      long_parents=1, long_parent_split_total=3),
            DocRecord(filename="b.docx", law_name="乙法", chunks=6, parents=3),
        ]

        summary = summarize(records, corpus="memory", mode="full")

        assert summary["full_mode"]["chunks"] == 16
        assert summary["full_mode"]["one_child_per_parent"] == (4 - 1 + 3) + 3

    def test_ingest_list_contains_only_selected_documents(self, tmp_path: Path) -> None:
        records = [
            DocRecord(filename="new.docx", law_name="某条例", publish_date="2021-07-30",
                      validity_status=3, article_count=10, province="山东省", city="菏泽市"),
            DocRecord(filename="old.docx", law_name="某条例", publish_date="2015-09-23",
                      validity_status=2, article_count=8),
            DocRecord(filename="broken.docx", error="ValueError", law_name="某条例"),
        ]
        select_versions(records)
        path = tmp_path / "ingest_list.csv"

        kept = write_ingest_list(records, str(path))

        assert kept == 1
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert [row["filename"] for row in rows] == ["new.docx"]
        assert rows[0]["province"] == "山东省"
        assert rows[0]["city"] == "菏泽市"
