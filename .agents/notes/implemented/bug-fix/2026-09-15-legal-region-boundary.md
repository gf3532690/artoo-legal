# Agent Note: Legal region names get a boundary, and city gets room

Status: implemented

## Problem

The city extractor guessed where a place name ends:

```python
_CITY_AT_START = re.compile(r"^([\u4e00-\u9fa5]{2,10}?)(?:市|自治州|地区|盟)")
...
city = match.group(1) + "市"
```

Two defects compounded.

**The suffix was thrown away and 「市」 appended unconditionally.** A match on
「自治州」 produced 「…族市」: 《德宏傣族景颇族自治州傣医药条例》 became
`德宏傣族景颇族市`. In `city_province.json`, 27 entries carried 「族市」 and the real
names were absent — `临夏回族自治州` was not in the table, `临夏回族市` was.

**The name boundary was a guess.** The non-greedy prefix grew until it hit the
first 「市」, which is often the start of the *next* word: 「阜新蒙古族自治县县城市容
和环境卫生管理条例」 → `阜新蒙古族自治县县城市` (33 bytes), 「河南蒙古族自治县城镇市容…」
 → `河南蒙古族自治县城镇市` (33 bytes), 「中华人民共和国城市维护建设税法」 →
`中华人民共和国城市`, 「人力资源市场暂行条例」 → `人力资源市`.

`scripts/build_city_province_map.py` used the same regex and the same `+ "市"`, so
the table was built from the same mistakes — the bad names were authoritative in
both the runtime extraction and the whitelist.

Measured on the 22,036-document ingest list:

- 2 documents **failed ingestion outright** with
  `MilvusException: length of varchar field city exceeds max length` — the `city`
  field was `VARCHAR(32)` and those two names were 33 bytes;
- 6 documents carried a city longer than 32 bytes; four more carried a name that
  was simply wrong;
- 889 documents had a city that changed once the rules were fixed;
-「按城市筛选」could never match a real prefecture: the stored values were
  `伊犁哈萨克市`, `凉山彝族市`, `克孜勒苏柯尔克孜市`, …

## Decision

`legal_region.city_at_start` returns the name **including its suffix** and applies
boundary guards, which are the corpus-measured discriminators rather than taste:

- the name may not end with another administrative suffix (`县`, `区`, `市`) —
  that is a prefix that swallowed the next word, not a place;
- the name may not end with `城镇` (`河南蒙古族自治县城镇市`), while `景德镇市` /
  `丰镇市` stay valid;
- a `市` match whose stem ends with `城` is rejected when the stem is 3+ characters:
  every legitimate `…城市` name in the corpus has a two-character stem (`塔城市`,
  `宣城市`, `晋城市`), while `中华人民共和国城市` / `北京城市` / `双鸭山城市` do not;
- a `市` immediately followed by `场`/`容`/`民`/`区` is a word (`市场`, `市容`),
  not a boundary;
- `地区` and `盟` are accepted only from the closed ten-name set
  (`_PREFECTURES`): 「中国公民往来台湾地区」「西部地区」「南疆地区」are textually
  identical to 「阿勒泰地区」, so guessing them produces a whole class of noise for
  three documents of upside;
- text that starts with a province returns `None` — the province branch owns it.

On top of the rule, the committed table is the **whitelist and the authority on
boundaries**: `city_of_title` tries the longest matching table entry first and only
falls back to the rule for cities the table has not seen. When a title starts with a
province, `resolve_region` strips it and takes the city from the whitelist **only** —
allowing the rule there produced 19 junk values (`城乡集市`, `不设区的市`,
`人民代表大会常务委员会盟`) against 164 recovered documents, so the rule is not
used there.

`scripts/build_city_province_map.py` builds the table with `city_at_start` and
nothing else. It must not call `city_of_title`: the table is what that function
consults, and running the builder that way fed the previous version's garbage back
in (measured: straight back to the old 365 entries, `临夏回族市` included).

`LEGAL_FILTER_FIELD_LENGTHS["city"]` moves from 32 to **64**: the corpus' own names
reach 33 bytes (`克孜勒苏柯尔克孜自治州`) and 45 bytes for county-level names
(`双江拉祜族佤族布朗族傣族自治县`), so 32 could only ever hold a truncated or
invented name.

## Alternatives considered

**Only widening the field.** Rejected: it stops the two hard failures and nothing
else — the wrong names stay, city filtering stays broken for prefectures, and the
field would be sized to fit garbage.

**Clamping or dropping over-long city values** instead of widening the column.
Rejected: the over-long values are the *correct* ones (`阿勒泰地区` is not the
problem, `克孜勒苏柯尔克孜自治州` is), so clamping would discard exactly the data the
filter needs.

**Replacing the derived table with a hand-written administrative-division table.**
Considered and not taken: the project deliberately derives the table from the corpus
so it stays reproducible from the data it ships with. This change keeps that
property and makes the derivation correct; a curated list remains an option if the
corpus ever needs divisions it does not contain.

**Keeping `地区`/`盟` in the rule and relying on the boundary guards.** Rejected on
measurement: the guards cannot separate 「阿勒泰地区」 from 「西部地区」/「南疆地区」 —
the distinction is vocabulary, not structure.

**Taking the city from the rule when the title starts with a province.** Rejected on
measurement: +164 correct cities with the whitelist, but 19 junk ones if the rule is
allowed there.

## Consequences

The table goes from 365 to 332 entries: 66 bad names removed (all the `…族市`,
`…县市`, `…城镇市`, `…城市市` forms) and 32 correct ones added (31 自治州 + `阿勒泰地区`).
Conflicts stay at 0.

The two documents that failed ingestion now ingest, and city values that used to be
garbage (`伊犁哈萨克市`, `凉山彝族市`, `克孜勒苏柯尔克孜市`) are now the real names, so
city filtering can match prefectures for the first time.

**The column width is load-bearing.** City values are now allowed to exceed 32 bytes
on purpose, so a deployment that keeps the old `VARCHAR(32)` will fail documents
again. Milvus fixes `max_length` at collection creation, so the collection must be
rebuilt (drop + recreate) before the next full ingest — the same rebuild the
`article`-granularity and filter-field changes already require.

Documents ingested before this change keep their old city values until they are
re-processed. Re-ingesting is the only way to refresh them; re-uploading the same
file does not work, because the server de-duplicates by `file_hash` and returns
`skipped`.

Only 3 documents in the ingest list carry a `地区` name and none carry a `盟`, so
dropping those suffixes from the rule costs almost nothing; `阿勒泰地区` is still
resolved through the table.

## Testing

`tests/test_legal_region.py` pins both directions: real names survive
(`厦门市`, `景德镇市`, `塔城市`, `德宏傣族景颇族自治州`, `阿勒泰地区`, `锡林郭勒盟`) and
over-capture is rejected (`阜新蒙古族自治县县城市容…`, `中华人民共和国城市维护建设税法`,
`人力资源市场暂行条例`, `中国公民往来台湾地区管理办法`, `山西省不设区的市和市辖区…`).
It also asserts the committed table keeps suffixes, contains no `…族市`/`…自治县`
names, and stays within the Milvus field length.

Verification against the corpus: the mapping was rebuilt from all 29,957 documents
(332 cities, 28 provinces, 0 conflicts), and a sampled audit of 800 documents
(`--sample 800 --seed 7`) reproduced the expected change set — 24 cities changed,
every one of them from a broken name to the correct one, with the longest city now
33 bytes.
