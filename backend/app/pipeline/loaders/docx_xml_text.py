"""直接从 ``word/document.xml`` 取段落文本的兜底实现。

python-docx 的段落模型看不到两类内容，两者在语料里都真实存在：

- 文本框里的文字（``w:drawing`` → ``w:txbxContent``）。例如《清远市城市市容和环境
  卫生管理条例》整部法条都在文本框里，走 python-docx 得到 0 字符；
- 表格内段落（``DocxLoader`` 已另行处理，这里同样按文档顺序包含）。

因此 loader 在结构化遍历取不到文字时用它兜底。抽取方式是按 ``<w:p>`` 切段、再取其中的
``<w:t>``，与 python-docx 的段落模型近似但不完全等价——这是它只作兜底、不作主路径的原因。
"""

from __future__ import annotations

import html
import io
import re
import zipfile

# 属性部分必须写成 ``(?:\s[^>]*)?``——用 ``[^>]*`` 会把 ``<w:tbl>`` / ``<w:tc>`` /
# ``<w:tab/>`` 这类同样以 ``t`` 开头的标签当成文本标签，把 XML 标记当正文喂出去。
_W_PARA = re.compile(r"<w:p(?:\s[^>]*)?>(.*?)</w:p>", re.DOTALL)
_W_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.DOTALL)

_DOCUMENT_PART = "word/document.xml"


def paragraphs_from_bytes(data: bytes) -> list[str]:
    """从 docx 字节里按文档顺序取非空段落文本。

    Raises:
        KeyError: 包里没有 ``word/document.xml``。
        zipfile.BadZipFile: 不是 zip。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read(_DOCUMENT_PART).decode("utf-8", "ignore")
    paragraphs = [
        text
        for raw in _W_PARA.findall(xml)
        if (text := html.unescape("".join(_W_TEXT.findall(raw))).strip())
    ]
    if paragraphs:
        return paragraphs
    # 没有 <w:p>（非典型 OOXML）时退化为文本流。
    text = html.unescape("".join(_W_TEXT.findall(xml)))
    return [line for line in text.splitlines() if line.strip()]
