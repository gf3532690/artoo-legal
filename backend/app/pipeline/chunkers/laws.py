"""LawsChunker - 法律文书切分器

按条款编号（第X条）和判决结构（本院认为/判决如下）切分为父块，
条款内容按段落切分为子块。

适用于：法律法规、判决书、裁定书等法律文书。
"""

import re

from app.pipeline.chunker import ChunkResult
from app.pipeline.chunker_router import BaseChunker, ChunkerFactory
from app.pipeline.legal_terms import ARTICLE_LINE_PATTERN, ITEM_LINE_PATTERN


# 法律文书结构分割正则：第X条、本院认为、判决如下、经审理查明等
_ARTICLE_PATTERN = re.compile(
    r'^(第[一二三四五六七八九十百千\d]+条'
    r'|本院认为'
    r'|判决如下'
    r'|裁定如下'
    r'|经审理查明'
    r'|事实与理由'
    r'|事实和理由'
    r'|诉讼请求)',
    re.MULTILINE,
)


class LawsChunker(BaseChunker):
    """法律文书切分器

    切分策略：
    - 父块：按条款编号（第X条）和判决结构关键词切分
    - 子块：每个父块内按段落（双换行或单换行非空行）切分
    """

    def chunk(self, text: str, metadata: dict | None = None) -> ChunkResult:
        """将法律文书切分为父子 chunk

        Args:
            text: 法律文书文本
            metadata: 可选元数据

        Returns:
            ChunkResult: 父块为条款/结构段，子块为段落
        """
        if not text or not text.strip():
            return ChunkResult(parent_chunks=[], child_chunks=[], parent_child_map={})

        stripped = text.strip()

        # 按法律结构标记切分父块。
        #
        # 条件化退化：语料里有 37 份文档（《刑法修正案》《全国人大常委会关于…的
        # 决定 / 规定》类）**完全没有**「第X条」结构，它们的一级结构是「一、二、三」。
        # 对这类文档若仍按条文切，整篇会变成单一父块，再按单行切子块就会把跨行的
        # 条目从中间切断（条目平均跨 2.56 行）。
        #
        # 为什么必须条件化：普通法律里「一、」是**条文内部**的项（《国籍法》第七条
        # 就含三个项），无条件把它当父块边界会把这些项从所属条文里拆出去。
        # 判定用共享的行首「第X条」正则（``legal_terms``），保证与元数据抽取同源。
        if ARTICLE_LINE_PATTERN.search(stripped):
            parent_chunks = self._split_by_pattern(stripped, _ARTICLE_PATTERN)
        else:
            parent_chunks = self._split_by_pattern(stripped, ITEM_LINE_PATTERN)

        # 对每个父块切分子块
        child_chunks: list[str] = []
        parent_child_map: dict[int, list[int]] = {}

        for parent_idx, parent_text in enumerate(parent_chunks):
            children = self._split_into_paragraphs(parent_text)
            child_indices = []
            for child_text in children:
                child_indices.append(len(child_chunks))
                child_chunks.append(child_text)
            parent_child_map[parent_idx] = child_indices

        return ChunkResult(
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            parent_child_map=parent_child_map,
        )

    def _split_by_pattern(self, text: str, pattern: re.Pattern[str]) -> list[str]:
        """按给定结构标记切分为父块

        每个匹配到的结构标记开始一个新的父块。
        标记之前的内容（如文书标题、当事人信息）作为第一个父块。

        Args:
            text: 待切分文本。
            pattern: 结构标记正则；``_ARTICLE_PATTERN`` 用于有条文结构的文书，
                ``ITEM_LINE_PATTERN`` 用于无条文结构的修正案 / 决定类文档。
        """
        matches = list(pattern.finditer(text))

        if not matches:
            # 没有法律结构标记，整段作为一个父块
            return [text]

        sections: list[str] = []

        # 第一个标记之前的内容（标题、当事人等）
        before = text[:matches[0].start()].strip()
        if before:
            sections.append(before)

        # 按标记切分
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section = text[start:end].strip()
            if section:
                sections.append(section)

        return sections if sections else [text]

    def _split_into_paragraphs(self, text: str) -> list[str]:
        """将父块内容按段落切分为子块

        优先按双换行切分，若只有单段则按单换行切分非空行。
        如果切分后只有一个段落，直接返回整个父块作为唯一子块。
        """
        # 先尝试双换行切分
        paragraphs = [p.strip() for p in re.split(r'\n\n+', text) if p.strip()]

        if len(paragraphs) > 1:
            return paragraphs

        # 双换行切分只得到一段，尝试按单换行切分
        lines = [line.strip() for line in text.split('\n') if line.strip()]

        if len(lines) > 1:
            return lines

        # 只有一行/一段，直接返回
        return [text.strip()]


# 注册到 ChunkerFactory
ChunkerFactory.register("laws", LawsChunker)
