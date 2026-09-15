"""docx 段落文本抽取的保真度：修订插入、``<w:br/>`` 折行、文本框嵌套段落。

三类问题都在实测语料里出现过，且都会让行首「第X条」失效、条文结构塌成一条：

- ``w:ins``：python-docx 的 ``Paragraph.text`` 看不到修订插入的 run。
  《中山市水环境保护条例》的「条」字就在插入块里，48 条只剩 1 条能被认出来；
- ``<w:br/>``：换行而不是新段落。旧的正则兜底把它吞了，
  《洛阳市矿产资源管理办法》78 个 ``<w:br/>``、7 个 ``<w:p>``，整部法条被压成一行；
- 嵌套 ``<w:p>``（文本框）：旧正则非贪婪匹配会在内层 ``</w:p>`` 提前收尾。

fixture 用 python-docx 造正常 docx，再把 ``w:body`` 换成手工 XML——这样构造的是
真实语料里那种「包合法、结构刁钻」的文档，而不是 python-docx 自己能生成的整齐结构。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import io
import zipfile
from pathlib import Path

import pytest
from docx import Document

from app.pipeline.legal_terms import ARTICLE_LINE_PATTERN
from app.pipeline.loaders.docx_loader import DocxLoader
from app.pipeline.loaders.docx_xml_text import paragraph_text, paragraphs_from_bytes


def _patch(document_xml: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
        for item in source.infolist():
            blob = source.read(item.filename)
            if item.filename == "word/document.xml":
                blob = document_xml.encode("utf-8")
            target.writestr(item.filename, blob)
    return buffer.getvalue()


def _build(tmp_path: Path, body: str) -> bytes:
    """造一份 body 被换成 ``body`` 的 docx，返回其字节。"""
    path = tmp_path / "fixture.docx"
    document = Document()
    document.add_paragraph("占位段落，随后整段被替换")
    document.save(path)
    data = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    start = xml.index("<w:body>") + len("<w:body>")
    end = xml.index("</w:body>")
    return _patch(xml[:start] + body + xml[end:], data)


def _article_count(content: str) -> int:
    return len(ARTICLE_LINE_PATTERN.findall(content))


class TestTrackedInsertions:
    def test_text_inside_w_ins_is_extracted(self, tmp_path: Path) -> None:
        """"条"字记成修订插入时不能丢——《中山市水环境保护条例》就是这种形态。"""
        body = (
            '<w:p><w:r><w:t>第一</w:t></w:r>'
            '<w:ins w:id="1" w:author="卢颖东"><w:r>'
            '<w:t xml:space="preserve">条 </w:t></w:r></w:ins>'
            '<w:r><w:t>为了保护和改善水环境，制定本条例。</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>第二</w:t></w:r>'
            '<w:ins w:id="2" w:author="卢颖东"><w:r>'
            '<w:t xml:space="preserve">条 </w:t></w:r></w:ins>'
            '<w:r><w:t>本条例适用于本市行政区域。</w:t></w:r></w:p>'
        )
        path = tmp_path / "ins.docx"
        path.write_bytes(_build(tmp_path, body))

        content = DocxLoader().load(str(path)).content

        assert "第一条 为了保护和改善水环境，制定本条例。" in content
        assert _article_count(content) == 2

    def test_deleted_text_is_not_extracted(self, tmp_path: Path) -> None:
        """修订删除的 ``w:delText`` 在 Word 里已不可见，不能当成正文。"""
        body = (
            '<w:p><w:r><w:t>第一条 保留的正文</w:t></w:r>'
            '<w:del w:id="3" w:author="卢颖东"><w:r>'
            '<w:delText>第二条 已删除的正文</w:delText></w:r></w:del></w:p>'
        )
        path = tmp_path / "del.docx"
        path.write_bytes(_build(tmp_path, body))

        content = DocxLoader().load(str(path)).content

        assert "第一条 保留的正文" in content
        assert "已删除的正文" not in content
        assert _article_count(content) == 1


class TestLineBreaks:
    def test_break_keeps_articles_on_separate_lines(self, tmp_path: Path) -> None:
        """一个 ``<w:p>`` 里用 ``<w:br/>`` 分行是语料里的常见形态。"""
        body = (
            "<w:p><w:r><w:t>第一条 甲</w:t><w:br/>"
            "<w:t>第二条 乙</w:t><w:br/>"
            "<w:t>第三条 丙</w:t></w:r></w:p>"
        )
        path = tmp_path / "br.docx"
        path.write_bytes(_build(tmp_path, body))

        content = DocxLoader().load(str(path)).content

        assert content.splitlines() == ["第一条 甲", "第二条 乙", "第三条 丙"]
        assert _article_count(content) == 3

    def test_page_break_does_not_add_a_line_break(self, tmp_path: Path) -> None:
        """分页符不是换行——python-docx 同样产出空串。"""
        body = (
            '<w:p><w:r><w:t>第一条 甲</w:t>'
            '<w:br w:type="page"/><w:t>同一条后半句</w:t></w:r></w:p>'
        )
        path = tmp_path / "pagebreak.docx"
        path.write_bytes(_build(tmp_path, body))

        content = DocxLoader().load(str(path)).content

        assert content == "第一条 甲同一条后半句"

    def test_tab_and_hyphen_are_mapped(self, tmp_path: Path) -> None:
        body = (
            "<w:p><w:r><w:t>甲</w:t><w:tab/><w:t>乙</w:t>"
            "<w:noBreakHyphen/><w:t>丙</w:t></w:r></w:p>"
        )
        path = tmp_path / "tab.docx"
        path.write_bytes(_build(tmp_path, body))

        content = DocxLoader().load(str(path)).content

        assert content == "甲\t乙-丙"


class TestNestedParagraphs:
    _TEXT_BOX_XML = (
        '<w:p xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        "<w:r><mc:AlternateContent><mc:Fallback>"
        '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
        '<v:textbox><w:txbxContent xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:p><w:r><w:t>第一条 文本框里的条文</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>第二条 仍在文本框里</w:t></w:r></w:p>"
        "</w:txbxContent></v:textbox></v:shape></w:pict>"
        "</mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )

    def test_nested_paragraphs_stay_on_separate_lines(self, tmp_path: Path) -> None:
        """文本框里的多条不能被拼成一行，否则只有第一条能被认出来。"""
        path = tmp_path / "textbox.docx"
        path.write_bytes(_build(tmp_path, self._TEXT_BOX_XML))

        content = DocxLoader().load(str(path)).content

        assert _article_count(content) == 2, content
        assert "第一条 文本框里的条文" in content
        assert "第二条 仍在文本框里" in content

    def test_nested_paragraph_is_not_emitted_twice(self, tmp_path: Path) -> None:
        """嵌套段落已并入父段落，`paragraphs_from_bytes` 不能再单独产出一份。"""
        paragraphs = paragraphs_from_bytes(_build(tmp_path, self._TEXT_BOX_XML))

        joined = "\n".join(paragraphs)
        assert joined.count("第一条 文本框里的条文") == 1
        assert joined.count("第二条 仍在文本框里") == 1

    def test_text_after_a_text_box_keeps_its_own_line(self, tmp_path: Path) -> None:
        """文本框后面的外层正文必须另起一行。

        嵌套段落只补前导换行时，外层段落剩下的正文会粘在文本框最后一行上，后一条的行首
        锚点就失效了——实测《衢州市农村住房建设管理条例》这类文档因此少认 5-6 条。
        """
        body = (
            '<w:p xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            "<w:r><w:t>第一条 位于文本框之前</w:t></w:r>"
            "<w:r><mc:AlternateContent><mc:Fallback>"
            '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
            '<v:textbox><w:txbxContent xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:p><w:r><w:t>第二条 位于文本框之内</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape></w:pict>"
            "</mc:Fallback></mc:AlternateContent></w:r>"
            "<w:r><w:t>第三条 位于文本框之后</w:t></w:r></w:p>"
        )
        path = tmp_path / "textbox_then_text.docx"
        path.write_bytes(_build(tmp_path, body))

        content = DocxLoader().load(str(path)).content

        assert _article_count(content) == 3, content
        assert content.splitlines() == [
            "第一条 位于文本框之前",
            "第二条 位于文本框之内",
            "第三条 位于文本框之后",
        ]


class TestFallbackExtraction:
    def test_fallback_handles_break_separated_articles(self, tmp_path: Path) -> None:
        """兜底路径同样要认 ``<w:br/>``——旧的正则实现会把它压成一行。"""
        body = (
            "<w:p><w:r><w:t>第一条 甲</w:t><w:br/>"
            "<w:t>第二条 乙</w:t><w:br/>"
            "<w:t>第三条 丙</w:t></w:r></w:p>"
        )

        paragraphs = paragraphs_from_bytes(_build(tmp_path, body))

        assert len(paragraphs) == 1
        assert _article_count(paragraphs[0]) == 3

    def test_fallback_raises_on_non_xml_document_part(self, tmp_path: Path) -> None:
        """``document.xml`` 不是良构 XML 时抛 XMLSyntaxError，由调用方自行决定怎么兜。"""
        path = tmp_path / "broken.docx"
        path.write_bytes(_build(tmp_path, "<w:p><w:r><w:t>未闭合</w:t></w:r>"))

        with pytest.raises(Exception):
            paragraphs_from_bytes(path.read_bytes())


class TestParagraphTextUnit:
    def test_paragraph_text_reads_element_directly(self, tmp_path: Path) -> None:
        from lxml import etree

        element = etree.fromstring(
            '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:r><w:t>甲</w:t><w:br/><w:t>乙</w:t></w:r></w:p>"
        )

        assert paragraph_text(element) == "甲\n乙"
