# Agent Note: 没有状态就是未标注，不是空

Status: implemented

[English](2026-09-16-legal-missing-validity-status-is-unlabelled.md) | 中文

## Problem

`validity_status` 只有一个来源：文档自己的 `docProps/custom.xml`。爬取语料每份都带它
（29,957/29,957），但任何不是来自那份登记表的文档——用户上传的文件——都没有这个属性。实测一份
真实上传（《中华人民共和国城市维护建设税法_20200811.docx》）：它的 `custom.xml` 里只有 WPS 的
样板字段（`ICV`、`KSOProductBuildVer`），`core.xml` 里是作者与修订信息。

这样的文档被存成了 `validity_status = NULL`，于是本来该有状态的地方是空白：列表里没有徽标，
更要紧的是**按「未标注」筛不出来**——而"源文件从没给它标过"恰恰就是未标注的意思。现场反馈是
「我上传了一个法条文件，解析完成后他怎么没有任何状态」。

## Decision

被当作法条文档解析、而源文件又没给状态的文档，落库为 **`0` 未标注**，并且**两处都写**：
`documents.validity_status` 与每个子块的 `metadata.validity_status`。

`NULL` 此后只剩一个含义：**还不是一份解析完成的法条文档**（未解析完成）。在本部署里不存在第三种
情况——所有知识库都是法条库（创建时统一兜底 `chunker_type=laws`，见 `api/knowledge_base.py`），
所以"已完成却完全没有状态"这一态不可能存在。

存量数据由 `scripts/backfill_document_validity.py` 的第二步补齐：把该列仍为空的已完成文档
（以及它们的子块）写成 `0`。

## Alternatives considered

**保留 NULL，只在界面上显示成未标注。** 否决：这个值不只是展示细节。文件列表用它做**等值筛选**，
检索把它水合进 `results[].metadata`。只改显示会让徽标与筛选直接打架——文件上写着未标注，而
「只看未标注」不返回它。

**改在读取时替换（API 层把 NULL 当 0）。** 否决：同一个理由的反向版本，而且更贵——列表 SQL、
检索水合、法条详情端点都得各自替换一遍，`NULL` 也会失去它唯一有用的含义。入库处写一次更便宜，
也让 `NULL` 保持诚实。

**交给「更新状态」手动补。** 作为默认行为否决：手动路径是给纠错用的，而"我传了一份法条，它什么
都不显示"不该由用户手工修——机器答得出来：源文件没给这份文档标状态。

**只对全局库默认，个人库留空。** 否决：这里每个知识库都是法条库，而同一份文件的状态会因为"传到
了哪个库"而不同，比另外两种做法都糟。

## Consequences

对一份已完成的文档，`validity_status` 现在总是有值——列表里如此，`results[].metadata` 里也是。
把"键缺失"当作"未知"的消费方，应把 `0` 读作未标注（字典就是这么定义的）。其余字段仍遵循
"读不到就整键缺失"的约定。

真实取值仍然只来自 `custom.xml`，默认值只对"压根没有标签"的文档生效。

重新解析会按文档自身属性重算，所以手改值扛不过它——这一点与本次改动之前一致。

## Verification

`tests/test_legal_document_status_filter.py` 钉住解析口径（空列表 / 全为 null → `0`；真实取值
包括 `0` 与 `-1` 原样通过）与抽取器产出的字段字典（头信息没有状态时它写 `0`，不是 `None`）。
`tests/test_legal_metadata.py` 的 `test_extractor_emits_null_keys_without_props` 已更新——它钉的
是旧的 NULL 行为，现在记录"`validity_status` 是唯一一个有落库默认值的键"。

本地栈实测：

```text
用户上传的那份（custom.xml 只有 ICV + KSOProductBuildVer）
  回填前   documents.validity_status = NULL   没有徽标，按「未标注」筛不出来
  回填后   documents.validity_status = 0      4 个子块全部带 0
  过滤 validity_status=0 → 能查到它

一份全新的上传（经 lite API，完全不涉及回填）
  解析完成后 validity_status = 0，4 个子块全部为 0
```
