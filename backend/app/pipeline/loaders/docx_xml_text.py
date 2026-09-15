"""段落文本抽取：一处语义，两条入口。

**为什么不用 python-docx 的 ``Paragraph.text``**：它只收 ``w:p`` 的直接 ``w:r`` 子元素，
于是看不到真实存在于语料里的两类文字——

- ``w:ins`` 里的 run（修订插入）。实测《中山市水环境保护条例》的「条」字被记成修订插入，
  于是条文都变成「第三水环境保护应当坚持……」，48 条只剩 1 条能被行首「第X条」认出来，
  条文结构整体塌掉；全语料 45 份文档带 ``w:ins``，其中 4 份真的丢条文；
- 文本框（``w:drawing`` → ``w:txbxContent``）与 ``w:sdt`` 等容器里的 run。最典型的
  《清远市城市市容和环境卫生管理条例》整部法条都在文本框里，python-docx 读到 0 字符。

**为什么不用正则扒 XML**：这是本模块改版前的做法，它有两个更隐蔽的坑——

- ``<w:br/>`` 是换行而不是新段落。法条语料里大量文档用它分行（《洛阳市矿产资源管理办法》
  78 个 ``<w:br/>``、只有 7 个 ``<w:p>``），旧做法只按 ``<w:p>`` 切段再拼接 ``<w:t>``，
  会把整部法条压成一行，行首「第X条」随即失效（抽样 2,000 份里 1.7% 中招）；
- ``<w:p>`` 可以嵌套（文本框就在里面），非贪婪正则会在内层 ``</w:p>`` 提前收尾，
  丢掉段落（全量 567 份 / 2.6%）。

所以这里用 lxml 走一遍文档树（lxml 本就是 python-docx 的依赖），把「什么算一个段落的文字」
收敛成 :func:`paragraph_text` 一处定义：主路径 ``DocxLoader`` 与兜底路径
:func:`paragraphs_from_bytes` 共用它，两条路径的语义不会再分叉。

字符映射对齐 python-docx（``CT_Br`` / ``CT_Cr`` / ``CT_NoBreakHyphen`` / ``CT_PTab`` /
``CT_Text``）：``w:br``（仅 ``textWrapping``）与 ``w:cr`` → 换行，``w:tab`` 与 ``w:ptab``
→ 制表符，``w:noBreakHyphen`` → ``-``。修订**删除**的文字在 ``w:delText`` 里，标签不同，
天然不会被收进来。
"""

from __future__ import annotations

import io
import zipfile

from lxml import etree

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_DOCUMENT_PART = "word/document.xml"

_PARAGRAPH = _W + "p"
_TEXT = _W + "t"
_BREAK = _W + "br"
_CARRIAGE_RETURN = _W + "cr"
_TAB = _W + "tab"
_POSITIONED_TAB = _W + "ptab"
_NO_BREAK_HYPHEN = _W + "noBreakHyphen"
_BREAK_TYPE = _W + "type"


def paragraph_text(paragraph) -> str:
    """取一个 ``w:p`` 元素的可见文字（含修订插入、超链接、文本框等容器里的内容）。

    参数是 lxml 元素（``w:p``）。嵌套在里面的段落（文本框）按换行接上——它们是独立成行的
    结构，直接拼接会让两条并排的条文粘成一行，行首「第X条」就再也认不出后一条。
    """
    return _inline_text(paragraph)


def _inline_text(element) -> str:
    """收集 ``element`` 子树里属于同一行的文字；嵌套段落两头都补换行。

    末尾的换行不能省：文本框后面常常还跟着外层段落自己的正文，少了这个换行，两段会粘在
    同一行，后一段的行首「第X条」就没了（实测《衢州市农村住房建设管理条例》这类文档
    会因此少认 5-6 条）。
    """
    parts: list[str] = []
    for child in element.iterchildren():
        tag = child.tag
        if tag == _PARAGRAPH:
            # 内层段落自成一行；两端补换行把它与外层正文隔开。
            parts.append("\n")
            parts.append(_inline_text(child))
            parts.append("\n")
        elif tag == _TEXT:
            parts.append(child.text or "")
        elif tag == _BREAK:
            # 分页/分栏符在纯文本里没有对应字符，python-docx 同样产出空串。
            if child.get(_BREAK_TYPE, "textWrapping") == "textWrapping":
                parts.append("\n")
        elif tag == _CARRIAGE_RETURN:
            parts.append("\n")
        elif tag in (_TAB, _POSITIONED_TAB):
            parts.append("\t")
        elif tag == _NO_BREAK_HYPHEN:
            parts.append("-")
        else:
            parts.append(_inline_text(child))
    return "".join(parts)


def _iter_own_paragraphs(node):
    """按文档顺序产出「不在其它段落内部」的 ``w:p``。

    表格、``w:sdt`` 等容器照常递归；段落本身不再下探——它内部的嵌套段落（文本框）已由
    :func:`paragraph_text` 收进父段落的文字里，再产出一次会重复。
    """
    for child in node.iterchildren():
        if child.tag == _PARAGRAPH:
            yield child
        else:
            yield from _iter_own_paragraphs(child)


def paragraphs_from_bytes(data: bytes) -> list[str]:
    """从 docx 字节里按文档顺序取非空段落文本。

    Raises:
        KeyError: 包里没有 ``word/document.xml``。
        zipfile.BadZipFile: 不是 zip。
        lxml.etree.XMLSyntaxError: ``document.xml`` 不是良构 XML。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read(_DOCUMENT_PART)
    root = etree.fromstring(xml)

    paragraphs = [
        text for element in _iter_own_paragraphs(root)
        if (text := paragraph_text(element).strip())
    ]
    if paragraphs:
        return paragraphs

    # 没有 <w:p>（非典型 OOXML）时退化为文本流：整棵树按同一套字符映射拼出来再按行切。
    return [line for line in paragraph_text(root).splitlines() if line.strip()]
