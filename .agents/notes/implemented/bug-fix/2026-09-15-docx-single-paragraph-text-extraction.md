# Agent Note: Docx paragraph text extraction is one implementation

Status: implemented

## Problem

Two text extractors coexisted in the docx loader, and each was blind to text the
other could see. Both failures are silent: the document ingests fine, it just
arrives without article structure, and the chunker then slices it by size.

**The structured walk used `Paragraph.text`**, which reads only a paragraph's
direct `w:r` children. Runs inside `w:ins` — Word's tracked insertions — are
invisible. In 《中山市水环境保护条例》 the 「条」 of every article sits inside an
insertion, so every article reads 「第三水环境保护应当坚持……」 and only 1 of 48
was recognised; the document reached the library as 24 equally-sized chunks with
no `article_label` and no `article_number`. On the 22,036-document ingest list,
45 documents carry `w:ins` and 4 of them lose article markers (75 markers).

**The XML fallback was regex-based** and had two blind spots of its own. It
matched `<w:p>` bodies non-greedily, so a nested paragraph — which is how a text
box is expressed — ended the match early and dropped the rest of the paragraph
(567 documents, 2.6%). And it joined `<w:t>` without mapping `<w:br/>`, which is
how a large part of the corpus separates articles: 《洛阳市矿产资源管理办法》 has
78 `<w:br/>` and only 7 `<w:p>`, so the whole statute collapsed onto one line and
the line-anchored 「第X条」 matched once. A 2,000-document sample put that failure
at 1.7%.

The previous note recorded the fallback as "deliberately all-or-nothing" and
accepted that mixed paragraph/text-box bodies lose the text-box part. That
statement is superseded here: the fallback was not only narrow, it was lossy on
the documents it did reach.

## Decision

`docx_xml_text.paragraph_text(element)` is the single definition of "the text of
one `w:p`". It walks the element tree with lxml and mirrors python-docx's
character mapping (`w:br` with `textWrapping` and `w:cr` → newline;
`w:tab`/`w:ptab` → tab; `w:noBreakHyphen` → `-`), but collects the whole subtree
rather than direct children. It therefore sees runs inside `w:ins`,
`w:hyperlink`, `w:sdt`, and nested paragraphs (`w:drawing` → `w:txbxContent`).
Tracks-changes deletions use `w:delText`, a different tag, so they are excluded
without a special case.

Nested paragraphs are wrapped in newlines on **both** sides. The trailing one is
not cosmetic: outer-paragraph text often follows a text box, and with only a
leading newline that text welds onto the text box's last line and loses its
line-anchored article marker — measured on 《衢州市农村住房建设管理条例》 as 6
articles.

`DocxLoader._blocks_of` calls this function instead of `Paragraph.text`.
`paragraphs_from_bytes` becomes an lxml tree walk over the same function and
returns paragraphs that are not themselves inside another paragraph, so text-box
content is counted once, inside its parent. No regexes remain in the extraction
path. `lxml` moves from a transitive dependency of python-docx to a declared one.

## Alternatives considered

**Promoting the XML fallback to the primary path.** Rejected on measurement: it
is the weaker extractor. It loses text on 1.7% of documents that separate
articles with `<w:br/>` and on 2.6% whose paragraphs nest. Its one advantage —
reaching text boxes — is covered by the tree walk.

**Triggering the fallback on a "degraded" signal** (extract both ways, trust
whichever recognises more article markers). Rejected: it is a heuristic layered
on two lossy extractors, it leaves the fallback's own defects in place for the
documents that reach it, and it costs a second extraction pass on every document.
The shared implementation removes the reason to compare.

**Special-casing `w:ins` inside the structured walk only.** Rejected as too
narrow: collecting runs under `w:ins` already requires walking the paragraph
subtree, so the marginal cost of using that walk for text boxes as well is
nearly zero, while leaving the fallback on regexes would keep a second, weaker
implementation of the same concept.

## Consequences

Corpus impact is bounded and one-directional. Re-running the full ingest list
through the loader (structure walk, else fallback; the fallback implemented both
ways for the comparison) moves article markers from 785,002 to 785,079: **+77
markers, all of them in the 4 tracked-change documents**, with **0 regressions**
and **0 documents whose text got shorter**. The four are 《中山市水环境保护条例》
(1 → 48), 《晋中市中小学校幼儿园规划建设条例》 (9 → 36),
《福州市烟花爆竹销售和燃放管理办法》 (20 → 22) and 《福州市建筑垃圾管理规定》
(28 → 29).

The fallback is now rarely reached: all 40 documents on the ingest list that
contain text boxes are read by the structured walk, so it only serves atypical
packages that python-docx cannot model at all. It stays because that failure mode
is real, but it is no longer the mechanism by which text boxes are recovered.

Only those 4 documents need to be ingested again. Every other document produces
byte-identical text to before, so a full re-ingest is not required — but any
document ingested before this change keeps the old text, and the audit's
`article_count` will disagree with the library's chunk counts until the 4 are
re-ingested.

`paragraphs_from_bytes` now raises `lxml.etree.XMLSyntaxError` where the old
regex silently returned whatever it could match; `DocxLoader` reaches it only
after python-docx has already parsed the package, so a malformed
`word/document.xml` still surfaces as `ValueError` from the loader.

## Testing

`tests/test_docx_text_extraction.py` builds real docx files with python-docx and
replaces `w:body` with hand-written XML, so the fixtures have the shape the
corpus does rather than the tidy shape python-docx emits. It pins: text inside
`w:ins` being extracted (and `w:delText` not being extracted), `<w:br/>` keeping
articles on separate lines while a page break does not, tab and no-break-hyphen
mapping, nested paragraphs staying on their own lines, text *after* a text box
keeping its own line, nested paragraphs not being emitted twice, and the fallback
handling `<w:br/>`-separated articles. `tests/test_docx_loader_repair.py`
continues to cover repair, tables, merged cells and the text-box fallback.

Verification against the corpus: the comparison above ran over all 22,036
documents of `ingest_list.csv` with 0 read failures.
