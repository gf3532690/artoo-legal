# Agent Note: 法条文档级元数据改用 docx 内嵌属性

Status: implemented

## Problem

文档级法条字段此前全部来自正文。`pipeline/legal_metadata.py` 的
`parse_legal_header` 把「首行到第一个『YYYY年M月D日』行之前」的全部行拼成
`law_name`，并从同一个括注行里读 `issuing_authority` 与 `publish_date`。语料实测
有两个事实让这条启发式很脆：

- 345 份样本里有 34 份（9.9%）标题跨两到三行，于是「取首行」会静默产出
  「全国人民代表大会常务委员会关于」。
- 日期行缺失时该规则没有任何兜底；而实际要入库的爬取语料远大于 345 份样本
  ——29,957 份 `.docx`，其中 3,824 份（12.8%）正文头部根本没有可解析的日期。

同时每一份文件都把同样的字段存成了 OOXML 文档属性：`docProps/custom.xml` 有
20 个业务字段（`title` / `authority` / `publish_date` / `effective_date` /
`validity_status` / `law_type` / `external_id` 等），`docProps/core.xml` 镜像了
`title` / `creator` / `subject` 以及日期摘要。全语料实测覆盖率：`title` 与
`authority` 100%，`publish_date` 87.2%，`effective_date` 75.2%。

## Decision

新增 `pipeline/docx_meta.py`，用标准库（`zipfile` + `xml.etree`）读这两个
`docProps` 部件，返回 `DocxProps` 数据类。`DocumentPipeline.process_to_vectors`
在处理 `docx` 输入时于 Load 之后、法条预处理之前调用它，再把结果传给
`analyze_legal_document(text, props=...)`。

优先级是**权威来源优先、启发式兜底**，且**逐字段**合并：

- `law_name`：`props.title` → 「首行到日期行之前拼接」。
- `issuing_authority`：`props.authority` → 括注剥动词。
- `publish_date`：`props.publish_date` → 括注。

`publish_date` 上两个来源并不是冗余关系：正文括注的第一个日期是**法条文本的通过
日期**，而 `props.publish_date` 标识的是**该文件实际承载的版本**。300 份抽样里有
196 份正因此不同（例如山东省道路运输条例：正文 `2010-11-25`，属性 `2022-03-30`）。
要对版本排序，属性值才是正确的键。

`LegalDocumentHeader` 新增 `meta_source`（`rule` / `docx-props` / `rule+docx`），
由 pipeline 打日志。读取层约束：任何失败都返回 `None`——非 zip、缺 `docProps`、
部件损坏一律降级，绝不因此让入库失败；单个部件损坏只丢该部件；值元素按子元素
通用读取，不硬编码 `vt:lpwstr`；不是 `YYYY-MM-DD` 的日期不采信；
`validity_status` 只转 `int`，不推断枚举含义。

读取层一次返回 8 个字段。本次只消费上面 3 个身份字段；其余 5 个
（`effective_date` / `validity_status` / `law_type` / `external_id` /
`source_code`）随后也已接入，记录见
[chunk_metadata 中的法条版本与溯源字段](../architecture/2026-09-14-legal-version-provenance-fields.zh.md)。

## Alternatives considered

**用 python-docx 解属性。** 不可行：`DocxLoader` 已经在用 python-docx，但它只暴露
`core_properties`，没有自定义属性 API，而业务字段都在 `docProps/custom.xml`。

**改由 `DocxLoader` 返回属性，不新增模块。** 否决：`LoadResult.metadata` 是所有
文件类型共享的 loader 契约，而文档属性不是抽取出的内容；KB 路径与会话路径都会
继承到多数 loader 产不出的字段。

**正文解析为主、属性作兜底。** 否决：实测覆盖率方向相反。属性对 `title` 与
`authority` 是 100%，而正文解析在 `publish_date` 上能到 99.83%，只是因为缺属性的
3,824 份里有 3,773 份仍有日期行。逐字段合并即可让启发式只补空缺。

**加配置开关。** 否决：本次是纯增量。属性缺失或不可读时自动回退到原有行为，加开关
只会多一个要维护的状态，且不改变任何结果。

**本次就把 8 个字段全部落库。** 本次否决：其中 5 个是新的 `chunk_metadata` 键，
属于持久化契约变更。现在读、以后消费，可以把那个决策单独拆出来。

## Consequences

凡是文件自带属性的场景，`law_name` / `issuing_authority` / `publish_date` 现在
直接来自文件本身，消除了正文启发式原有的静默失败类型：标题跨行时得到一个看起来
正常却错误的法名，或日期行缺失时无从兜底。

改动在运行时与存储上都是纯增量：`chunk_metadata` 键、Milvus 字段、响应字段全无
变化；不带 `props` 的 `parse_legal_header(text)` 行为与改造前逐字段一致；非 docx
输入与没有 `docProps` 的 docx 保持原行为。

`meta_source` 在 header 上保留字段来源，并与 5 个版本与溯源字段一起落进
`chunk_metadata`。

`.doc` 会完全丢失属性：loader 经 LibreOffice 转换，而转换会丢掉 `docProps`。
当前语料 100% 是 `.docx`（压缩包 59,914 个 entry），因此今天没有受影响的输入。

## Testing

`tests/test_docx_meta.py` 在 `tmp_path` 里现造合成 docx，钉住读取层的边界：
从 `custom.xml` 读已知字段、只有 `core.xml` 镜像时兜底、`custom.xml` 覆盖
`core.xml`、缺 `docProps`、非 zip 输入、文件不存在、非 `lpwstr` 值类型、空值元素、
非 ISO 日期、未知键落进 `extra`、以及单部件损坏不影响另一部件。

`tests/test_legal_metadata.py` 新增 `TestDocxPropsOverride` 覆盖优先级规则：全字段
覆盖、部分属性走规则兜底、`props=None` 时规则结果不变、条文结构判定仍由正文决定、
以及 `analyze_legal_document` 的端到端路径。

此外还拿爬取语料抽了 300 份，把两条路径都跑了一遍：300 份全部读到属性，
`meta_source` 为 `docx-props` 的 264 份、`rule+docx` 的 34 份。相对只用正文解析，
合并后 `law_name` 有 35 份发生变化（零宽字符，以及把后续正文一起吞进法名的标题），
`issuing_authority` 有 298 份发生变化。

300 份里有 2 份 python-docx 打不开——一份是 macro-enabled 文档，一份声明了不存在
的 `userCustomization/customUI.xml` 部件。这是 `DocxLoader` 既有的限制，不是本次
改动引入的回归：属性读取只解 `docProps`、完全不走 python-docx，因此这两份文件仍
取到了元数据。
