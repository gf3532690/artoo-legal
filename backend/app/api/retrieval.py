"""检索测试接口

提供纯检索测试能力（不经过 LLM 生成），用于调参与召回质量验证：
- direct 模式：仅稠密向量检索，最快，观察纯语义召回
- hybrid 模式：三路混合检索（Dense + Sparse + BM25）+ RRF + Rerank + MMR + 父块扩展，
  并返回检索链路各阶段的中间信号（各路召回数、漏斗、每条结果的多维分数与命中路由），
  这是 Langfuse 等被动观测工具无法提供的"主动探针"能力，专为调参设计。

TODO: [准度风险] 当知识库中大量表格 chunk（如 CSV 5万+条）与少量文档 chunk 共存时，
  表格 chunk 可能在检索时"淹没"其他文档结果。后续可通过：
  1. 检索时加 doc_id / file_type 过滤
  2. 按文档类型加权评分
  3. 调整 top_k 策略
  来缓解。
"""

import logging
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.models.manager import get_model_manager
from app.pipeline.legal_terms import CN_NUMERAL_CHARS, chinese_to_int
from app.retrieval.base import RetrievalResult
from app.retrieval.article_lookup import load_article_rows
from app.retrieval.factory import build_hybrid_retriever
from app.retrieval.filter import RetrievalFilter, law_types_for_levels
from app.pipeline.legal_metadata import VALIDITY_STATUS_LABELS, strip_content_prefix
from app.retrieval.legal_scope import resolve_global_legal_kb_ids
from app.retrieval.multi_kb import KBRetrievalConfig, MultiKBRetriever
from app.retrieval.vector import VectorRetriever
from app.storage.database import async_session
from app.storage.milvus import (
    MilvusClient,
    get_milvus_client,
)

from app.api.deps import require_authenticated
from app.auth.identity import IdentityContext
from app.auth.kb_scope import authorize_content_read

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/retrieval", tags=["Retrieval"])

# 分页窗口上限。分页靠「多取一页候选、再切片」实现，而候选池本身有上限
# （单库 hybrid 的 rerank 候选池见 retrieval config 的 ``rerank_candidate_k``，默认 50），
# 窗口超过它就无法保证「第 N 页」真是排序里的第 N 页。这里固定上限并明确拒绝，
# 而不是悄悄返回空页。
_MAX_PAGINATION_WINDOW = 100

# 语义 / 精确两种模式。exact = 「法名 + 条号」直接查元数据，不参与相似度排序。
MATCH_MODE_SEMANTIC = "semantic"
MATCH_MODE_EXACT = "exact"

# 效力状态**不在这里过滤**。早先的默认口径是"检索排除已废止/已失效"，现改为：检索一律返回
# 全部，把状态（原值与中文描述）随结果一起下发，由调用方自己决定怎么用——过滤发生在调用方
# 手里的前提是它先拿得到状态。枚举与标签的唯一真源是
# ``pipeline.legal_metadata.VALIDITY_STATUS_LABELS``。

# 「第X条」引用：X 可以是中文数字（含零〇两）或阿拉伯数字。
_ARTICLE_REF = re.compile(rf"第\s*([{CN_NUMERAL_CHARS}\d]+)\s*条")

# 法名两侧可能带的书名号 / 引号 / 标点，解析前先剥掉。
_LAW_NAME_TRIM = "《》〈〉「」『』\"'“”‘’ 　:：,，、;；"


def parse_article_query(query: str) -> tuple[str | None, int | None]:
    """把「法名 + 条号」式查询拆成 ``(法名, 条号)``；拆不出返回 ``(None, None)``。

    支持「刑法第234条」「《中华人民共和国民法典》第一条」「民法典 第一百四十六条」等写法。
    只认第一处「第X条」——法名内部不会再出现条号，取第一处即可。
    """
    text = (query or "").strip()
    match = _ARTICLE_REF.search(text)
    if not match:
        return None, None
    numeral = match.group(1)
    number = int(numeral) if numeral.isdigit() else chinese_to_int(numeral)
    if number is None or number <= 0:
        return None, None
    law_name = text[: match.start()].strip().strip(_LAW_NAME_TRIM).strip()
    return (law_name or None), number

# ============================================================
# 请求/响应模型
# ============================================================


class RetrievalTestRequest(BaseModel):
    """检索测试请求"""

    query: str = Field(..., min_length=1, description="查询文本")
    knowledge_base_id: str | None = Field(
        default=None, description="单知识库 ID（与 kb_ids 二选一；两者可同时省略而只传 session_id）"
    )
    kb_ids: list[str] | None = Field(
        default=None, description="多知识库联合检索的知识库 ID 列表（与 knowledge_base_id 二选一）"
    )
    session_id: str | None = Field(
        default=None,
        description="会话 ID：把该会话已上传的附件作为一路检索源并入召回（需为调用者本人会话）。"
        "可单独使用，也可与知识库联合。",
    )
    mode: str = Field(
        default="hybrid",
        description="检索模式: direct（仅稠密）/ hybrid（三路混合 + 平台开启图谱时并入图谱第四路）。"
        "注意：多源（多库或含会话附件）统一按 hybrid 混合召回口径执行，direct 仅在单库单源时生效。",
    )
    # 法条库部署的默认值：5（上游为 10）。字段名与语义保持不变，
    # 只调默认值，见 docs/legal-recall-implementation-plan.md 决策 #8。
    top_k: int = Field(default=5, ge=1, le=100, description="返回结果数量")
    page: int = Field(
        default=1, ge=1,
        description="页码（从 1 开始，每页 top_k 条）。分页窗口 page×top_k 上限 100——"
        "召回候选池本身有上限，更深的翻页没有意义。",
    )
    match_mode: str = Field(
        default=MATCH_MODE_SEMANTIC,
        description="semantic（默认，语义召回）/ exact（「法名 + 条号」精确检索）。"
        "exact 命中时直接按法条元数据返回该条，不参与相似度排序；没命中会退回 semantic，"
        "并在响应的 fallback_reason 里说明原因。",
    )
    # ── 法条过滤（PRD《法条检索基础API》的"按效力层级 / 按省份城市筛选"）──
    # 层级用枚举而不是语料原始 law_type：口径变化只需改应用层映射，不用重建索引。
    law_levels: list[str] | None = Field(
        default=None,
        description="限定效力层级，取自 constitution / law / decision / "
        "administrative_regulation / judicial_interpretation / local_regulation / "
        "supervision_regulation；留空表示不限层级",
    )
    province: str | None = Field(
        default=None, description="限定省份（如「江西省」）；国家层面法规始终保留"
    )
    city: str | None = Field(
        default=None, description="限定城市（如「景德镇市」）；国家层面法规始终保留"
    )

    def resolve_kb_ids(self) -> list[str]:
        """归并 ``kb_ids`` 与单选 ``knowledge_base_id`` 为去重后的知识库 ID 列表（保持顺序）。

        kb_ids 优先；仅传 knowledge_base_id 时退化为单元素列表；两者皆空返回空列表
        （此时必须提供 session_id，否则无检索范围）。
        """
        raw = list(self.kb_ids) if self.kb_ids else (
            [self.knowledge_base_id] if self.knowledge_base_id else []
        )
        seen: set[str] = set()
        deduped: list[str] = []
        for kb_id in raw:
            if kb_id and kb_id not in seen:
                seen.add(kb_id)
                deduped.append(kb_id)
        return deduped

    def to_filter(self) -> RetrievalFilter | None:
        """把层级/地域参数翻成检索过滤条件；未传任何过滤参数时返回 ``None``。"""
        law_types = law_types_for_levels(self.law_levels)
        if not (law_types or self.province or self.city):
            return None
        return RetrievalFilter(
            law_types=law_types or None,
            province=(self.province or "").strip() or None,
            city=(self.city or "").strip() or None,
        )

    def window(self) -> int:
        """分页窗口 = ``page × top_k``：一次取这么多候选，再切出本页。"""
        return self.top_k * self.page


class RetrievalResultItem(BaseModel):
    """单条检索结果（含多维分数与命中路由）"""

    chunk_id: str
    doc_id: str
    filename: str = ""
    # 命中来源类型，供前端选对原件接口：
    #   "knowledge_base" → 知识库文档，原件走 /api/documents/{doc_id}/raw
    #   "session"        → 会话附件，原件走 /api/sessions/{session_id}/files/{doc_id}/raw
    source_type: str = "knowledge_base"
    content: str
    child_content: str = ""
    score: float  # 最终分数（hybrid=composite，direct=稠密相似度）
    rrf_score: float | None = None  # RRF 融合分数（仅 hybrid）
    rerank_score: float | None = None  # Rerank 精排分数（仅 hybrid）
    routes: list[str] = Field(default_factory=list)  # 命中路由：dense/sparse/bm25
    metadata: dict = Field(default_factory=dict)


class RouteInfo(BaseModel):
    """单路检索召回统计"""

    name: str
    recalled: int
    enabled: bool = True


class FunnelStage(BaseModel):
    """检索链路单个阶段的结果数"""

    stage: str
    count: int


class RetrievalTrace(BaseModel):
    """检索链路追踪信息（仅 hybrid 模式返回）"""

    routes: list[RouteInfo]
    funnel: list[FunnelStage]


class RetrievalTestResponse(BaseModel):
    """检索测试响应"""

    query: str
    mode: str
    total: int
    elapsed_ms: int
    results: list[RetrievalResultItem]
    trace: RetrievalTrace | None = None
    degraded: bool = False  # 是否有检索源失败（多源场景才可能为 True）
    failed_source_count: int = 0  # 失败的检索源数量（多源场景；单源恒为 0）
    # 分页：total 仍是"本次返回条数"（保持既有语义），翻页判据看 has_more。
    page: int = 1
    page_size: int = 0  # = 请求里的 top_k
    has_more: bool = False  # 本页之后是否还有候选（多取一条探到，不是估计）
    # 实际生效的检索模式；exact 没命中时会退回 semantic 并给出原因。
    match_mode: str = MATCH_MODE_SEMANTIC
    fallback_reason: str | None = None
# ============================================================
# 接口实现
# ============================================================


def _get_milvus() -> MilvusClient:
    """获取 Milvus 客户端"""
    return get_milvus_client()


# ------------------------------------------------------------------
# 精确检索与分页的共用件
# ------------------------------------------------------------------


def _validity_status_label(value: int | None) -> str | None:
    """效力状态的中文描述；字典外的取值返回 ``None``（原值照发，不编标签）。

    真源是数据源字典（:data:`VALIDITY_STATUS_LABELS`）；客户端各抄一份的下场是抄错——这个
    枚举被读反过一次（``0`` 是未标注、``-1`` 才是已失效）。
    """
    if value is None:
        return None
    return VALIDITY_STATUS_LABELS.get(value)


def _slice_page_items(
    items: list, body: RetrievalTestRequest
) -> tuple[list, bool]:
    """在**最终**的结果列表上切页。

    ``has_more`` 是用多取的那一条探出来的，不是估计——所以调用方必须先按
    ``window() + 1`` 取候选，把这一页真正要返回的条目都准备齐了再切页；切片放在任何
    还会丢掉条目的处理之前，都会把 ``has_more`` 和页码算错。
    """
    start = (body.page - 1) * body.top_k
    end = start + body.top_k
    return items[start:end], len(items) > end


async def _exact_retrieval(
    body: RetrievalTestRequest,
    rows: list[dict],
    *,
    global_kb_ids: list[str] | None,
) -> RetrievalTestResponse:
    """把精确命中的法条包成与语义召回一致的响应（含法条元数据与来源标记）。"""
    start = time.perf_counter()
    results = [
        RetrievalResult(
            chunk_id=row["chunk_id"],
            content=row["article_content"],
            score=1.0,
            doc_id=row["doc_id"],
            child_content=row["child_content"],
            metadata={},
        )
        for row in rows
    ]
    # 点名取那一条：结果照常带 validity_status 与中文描述，由调用方自行判断还算不算数。
    items = await _build_result_items(results, global_kb_ids=global_kb_ids)
    for item in items:
        item.routes = ["exact"]
    return RetrievalTestResponse(
        query=body.query,
        mode=MATCH_MODE_EXACT,
        total=len(items),
        elapsed_ms=int((time.perf_counter() - start) * 1000),
        results=items,
        trace=None,
        page=1,
        page_size=len(items),
        has_more=False,
        match_mode=MATCH_MODE_EXACT,
    )


async def _run_retrieval(
    body: RetrievalTestRequest, identity: IdentityContext
) -> RetrievalTestResponse:
    """执行纯检索召回（不经 LLM），供 ``/test`` 与 ``/search`` 共用同一实现。

    - ``direct``：仅稠密向量单路，最快，无 trace。
    - ``hybrid``：三路（Dense + Sparse + BM25）+ 可选图谱第四路 + RRF + Rerank + MMR +
      父块扩展，并返回链路追踪。图谱第四路经 ``build_hybrid_retriever`` 按全局开关 + 图存储
      可用性注入，与生产问答链路（chat）同口径；未开启图谱时行为与三路完全一致。
    """
    # 会话链路已随非召回链路移除（见方案 D7）。显式传入 session_id 时直接拒绝，
    # 而不是静默忽略——否则调用方会以为附件也参与了召回。
    if body.session_id:
        raise HTTPException(
            status_code=400,
            detail="本部署不支持 session_id：会话附件链路已移除，请改用 kb_ids",
        )

    # 枚举与分页窗口都显式校验：写错枚举不该静默退化成"不过滤 / 不分页"。
    if body.match_mode not in (MATCH_MODE_SEMANTIC, MATCH_MODE_EXACT):
        raise HTTPException(
            status_code=400,
            detail=f"match_mode 只支持 {MATCH_MODE_SEMANTIC} / {MATCH_MODE_EXACT}",
        )
    if body.window() > _MAX_PAGINATION_WINDOW:
        raise HTTPException(
            status_code=400,
            detail=f"分页窗口 page×top_k={body.window()} 超过上限 {_MAX_PAGINATION_WINDOW}",
        )

    kb_ids = body.resolve_kb_ids()

    # 法条库部署：**全局法条库默认并入检索范围**，调用方不必（也不应）自己传。
    # 并入位置在授权之前，让它像其它源一样走同一套读授权判定——单租户部署下
    # 全局库是 organization + read，同租户身份自然通过，不需要任何租户例外。
    # 见 docs/legal-recall-implementation-plan.md 的 D4 / D5 / D6。
    global_kb_ids = await resolve_global_legal_kb_ids()
    if global_kb_ids:
        # 去重且保持顺序：调用方若自己传了全局库 id，不会产生重复源。
        kb_ids = list(dict.fromkeys([*kb_ids, *global_kb_ids]))

    if not kb_ids:
        # 正常不会到这里：全局法条库若已引导，上面必然补入至少一个库。
        raise HTTPException(
            status_code=400,
            detail="未找到全局法条库，且未指定 knowledge_base_id / kb_ids",
        )

    # 触达 Milvus 前先校验：内容边界 + KB 读权限（跨租户/不可读 404）。
    await authorize_content_read(identity, kb_ids)

    # 精确检索：解析出「法名 + 条号」就直接按元数据取，不参与相似度排序。
    # 解析不出或库里没有 → 记下原因，退回语义召回并在响应里说明（不静默降级）。
    fallback_reason: str | None = None
    if body.match_mode == MATCH_MODE_EXACT:
        law_name, article_number = parse_article_query(body.query)
        if article_number is None:
            fallback_reason = "查询里没有可解析的「第X条」条号，已按 semantic 召回"
        else:
            rows = await load_article_rows(
                kb_ids=kb_ids, article_number=article_number, law_name=law_name
            )
            if rows:
                return await _exact_retrieval(body, rows, global_kb_ids=global_kb_ids)
            label = f"《{law_name}》" if law_name else ""
            fallback_reason = f"库里没有{label}第{article_number}条，已按 semantic 召回"

    # 多源（多库）走 MultiKBRetriever 混合召回；单库单源保留 direct/hybrid + trace。
    if len(kb_ids) > 1:
        return await _run_multi_source_retrieval(
            body, kb_ids, identity, global_kb_ids, fallback_reason=fallback_reason
        )

    return await _run_single_kb_retrieval(
        body, kb_ids[0], global_kb_ids, fallback_reason=fallback_reason
    )


async def _run_single_kb_retrieval(
    body: RetrievalTestRequest,
    kb_id: str,
    global_kb_ids: list[str] | None = None,
    *,
    fallback_reason: str | None = None,
) -> RetrievalTestResponse:
    """单库单源检索：保留原 ``direct`` / ``hybrid`` + trace 行为（零回归）。

    授权已在 ``_run_retrieval`` 前置完成，此处只负责召回与结果组装。
    """
    start = time.perf_counter()
    legal_filter = body.to_filter()
    expr = legal_filter.to_milvus_expr() if legal_filter else None
    # 多取一条用来探「还有没有下一页」，所以取 window+1 条候选、再切本页。
    fetch_k = body.window() + 1
    if body.mode == "direct":
        manager = get_model_manager()
        retriever = VectorRetriever(manager.embedder, _get_milvus())
        ranked = await retriever.search(body.query, kb_id, top_k=fetch_k, expr=expr)
        built = await _build_result_items(ranked, global_kb_ids=global_kb_ids)
        items, has_more = _slice_page_items(built, body)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return RetrievalTestResponse(
            query=body.query,
            mode="direct",
            total=len(items),
            elapsed_ms=elapsed_ms,
            results=items,
            trace=None,
            page=body.page,
            page_size=body.top_k,
            has_more=has_more,
            fallback_reason=fallback_reason,
        )

    # hybrid 模式：三路 + 可选图谱第四路（由工厂按门控注入）+ 链路追踪。
    # 纯检索召回接口跳过 rerank 软阈值过滤（apply_rerank_filter=False）：软阈值是问答链路
    # 防幻觉机制，会把短/泛 query 的低分但相关结果连兜底一起滤空；召回接口应返回 rerank 排序
    # 后的 top_k，由调用方参考 rerank_score 自行取舍。
    hybrid_retriever = await build_hybrid_retriever()
    ranked, trace_data = await hybrid_retriever.search_with_trace(
        body.query, kb_id, top_k=fetch_k, expr=expr, apply_rerank_filter=False
    )
    built = await _build_result_items(
        ranked, trace_data.get("per_result"), global_kb_ids=global_kb_ids
    )
    items, has_more = _slice_page_items(built, body)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    trace = RetrievalTrace(
        routes=[RouteInfo(**r) for r in trace_data.get("routes", [])],
        funnel=[FunnelStage(**f) for f in trace_data.get("funnel", [])],
    )
    return RetrievalTestResponse(
        query=body.query,
        mode="hybrid",
        total=len(items),
        elapsed_ms=elapsed_ms,
        results=items,
        trace=trace,
        page=body.page,
        page_size=body.top_k,
        has_more=has_more,
        fallback_reason=fallback_reason,
    )


async def _run_multi_source_retrieval(
    body: RetrievalTestRequest,
    kb_ids: list[str],
    identity: IdentityContext,
    global_kb_ids: list[str] | None = None,
    *,
    fallback_reason: str | None = None,
) -> RetrievalTestResponse:
    """多库联合检索：走 ``MultiKBRetriever`` 混合召回。

    各源同权（priority=1.0），最终顺序交由统一 rerank 决定；trace 返回 ``null``
    （多源不聚合单源链路信号），并以 ``degraded`` / ``failed_source_count`` 反映源失败情况。

    会话附件源已随会话链路移除（见方案 D7）。
    """
    start = time.perf_counter()
    kb_configs: list[KBRetrievalConfig] = [
        KBRetrievalConfig(kb_id=kb_id, priority=1.0) for kb_id in kb_ids
    ]

    if not kb_configs:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return RetrievalTestResponse(
            query=body.query,
            mode="hybrid",
            total=0,
            elapsed_ms=elapsed_ms,
            results=[],
            trace=None,
            page=body.page,
            page_size=body.top_k,
            fallback_reason=fallback_reason,
        )

    hybrid_retriever = await build_hybrid_retriever()
    multi_kb = MultiKBRetriever(hybrid_retriever)
    # 纯检索召回接口跳过 rerank 软阈值过滤（与单库路径一致）：召回接口返回 rerank 排序结果，
    # 不做问答链路的防幻觉软阈值截断，避免短/泛 query 被兜底也救不回而返回空。
    # 与单库路径同口径：多取一条探是否有下一页。
    multi_fetch_k = body.window() + 1
    multi_result = await multi_kb.search(
        body.query, kb_configs, top_k=multi_fetch_k, tenant_id=identity.tenant_id,
        filters=body.to_filter(), apply_rerank_filter=False,
    )
    built = await _build_result_items(multi_result.results, global_kb_ids=global_kb_ids)
    items, has_more = _slice_page_items(built, body)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    return RetrievalTestResponse(
        query=body.query,
        mode="hybrid",
        total=len(items),
        elapsed_ms=elapsed_ms,
        results=items,
        trace=None,
        degraded=multi_result.degraded,
        failed_source_count=len(multi_result.failed_kb_ids),
        page=body.page,
        page_size=body.top_k,
        has_more=has_more,
        fallback_reason=fallback_reason,
    )


@router.post("/test", response_model=RetrievalTestResponse)
async def retrieval_test(
    body: RetrievalTestRequest,
    identity: IdentityContext = Depends(require_authenticated()),
) -> RetrievalTestResponse:
    """纯检索测试：direct（稠密）/ hybrid（三路混合 + 可选图谱第四路 + 链路追踪）。

    不经过 LLM 生成，仅返回检索召回的 chunk 及其分数信号，主要用于前端调参页。
    与对外的 ``/search`` 行为一致（同一底层实现），保留此路径用于既有前端调用。
    """
    return await _run_retrieval(body, identity)


@router.post("/search", response_model=RetrievalTestResponse)
async def retrieval_search(
    body: RetrievalTestRequest,
    identity: IdentityContext = Depends(require_authenticated()),
) -> RetrievalTestResponse:
    """对外召回接口：direct（稠密）/ hybrid（三路 + 可选图谱第四路）。

    与 ``/test`` 能力一致（同一底层实现），独立路径供第三方集成直接调用，语义上是"检索召回"
    而非"测试"。hybrid 模式在平台开启图谱且图存储可用时自动并入图谱第四路，与生产问答链路
    的召回口径一致。可用代理 Key + ``X-External-User-Id`` 调用。

    检索范围三选一/可组合（至少其一）：
    - ``knowledge_base_id``：单知识库；
    - ``kb_ids``：多知识库联合检索；
    - ``session_id``：并入该会话（须本人）已上传附件作为一路检索源。

    单库单源保留 direct/hybrid 与完整 ``trace``；多源（多库或含会话附件）统一按 hybrid 混合
    召回，``trace`` 为 ``null``，并以 ``degraded`` / ``failed_source_count`` 反映源失败情况。
    """
    return await _run_retrieval(body, identity)


async def _build_result_items(
    results: list[RetrievalResult],
    per_result: dict | None = None,
    global_kb_ids: list[str] | None = None,
) -> list[RetrievalResultItem]:
    """将检索结果转换为响应格式，附带文件名与（可选的）链路分数信号。

    ``source_type`` 恒为 ``knowledge_base``：会话附件来源已随会话链路移除（见方案 D7）。
    原件获取由第三方前端按 ``doc_id`` 自行调用 ``/api/documents/{doc_id}/raw``，本响应不返回原件 URL。

    **不按效力状态过滤**：早先的默认口径是排除已废止/已失效，现已取消——检索一律返回全部，
    调用方拿到 ``validity_status`` 与中文描述 ``validity_status_label`` 后自己决定怎么用。
    过滤放在调用方手里的前提，是它先拿得到状态。
    """
    doc_ids = list({r.doc_id for r in results})
    doc_filenames: dict[str, str] = {}
    if doc_ids:
        async with async_session() as session:
            from app.schema.db import Document

            result = await session.execute(
                select(Document.id, Document.filename).where(Document.id.in_(doc_ids))
            )
            for row in result:
                doc_filenames[row.id] = row.filename

    # 法条元数据水合：按 chunk_id 批量取一次 Chunk，拿到 chunk_metadata 与 kb_id。
    # 与上面的文件名水合同属「一次批量查」模式——禁止按结果逐条查询（N 次单查）。
    chunk_ids = list({r.chunk_id for r in results})
    chunk_legal: dict[str, dict] = {}
    chunk_kb: dict[str, str] = {}
    if chunk_ids:
        from app.schema.db import Chunk

        async with async_session() as session:
            rows = await session.execute(
                select(Chunk.id, Chunk.kb_id, Chunk.chunk_metadata).where(
                    Chunk.id.in_(chunk_ids)
                )
            )
            for row in rows:
                chunk_kb[row.id] = row.kb_id
                if isinstance(row.chunk_metadata, dict):
                    chunk_legal[row.id] = row.chunk_metadata

    # 法条字段放进已有的 metadata 槽位，不新增顶层字段（响应模型保持不变）。
    #
    # 这里下发的是 PRD《法条检索基础API》要的产品字段：结果要带"效力层级"
    # （law_type）供调用方筛选与展示，要带发布/施行日期与时效状态供判断新旧，
    # 要带地域供地方性法规的展示与核对。数据早已在 chunk_metadata 里
    # （见 docs/legal-recall-implementation-plan.md §6.1），此处只是把它们露出来。
    _LEGAL_KEYS = (
        "law_name",
        "article_number",
        "article_label",
        "chapter",
        "law_type",
        "issuing_authority",
        "publish_date",
        "effective_date",
        "validity_status",
        "province",
        "city",
    )
    global_set = set(global_kb_ids or [])

    items: list[RetrievalResultItem] = []
    dropped = 0
    for r in results:
        trace_entry = (per_result or {}).get(r.chunk_id, {})
        source_type = "knowledge_base"

        metadata = dict(r.metadata or {})
        legal_raw = chunk_legal.get(r.chunk_id, {})
        for key in _LEGAL_KEYS:
            value = legal_raw.get(key)
            if value is not None:
                metadata[key] = value

        # 效力的中文描述随原值一起下发。枚举的真源是数据源字典（VALIDITY_STATUS_LABELS），
        # 客户端不该各抄一份——这个枚举被读反过一次（`0` 是未标注、`-1` 才是已失效）。
        # 字典外的取值**不编标签**：原值照发，缺一个描述好过编一个错的。
        label = _validity_status_label(metadata.get("validity_status"))
        if label is not None:
            metadata["validity_status_label"] = label

        # 法条 ID：由「文档 + 条号」派生，供调用方按法条取详情/去重，不需要额外入库字段。
        # 无条号的文档（修正案 / 决定类）不给，而不是给一个会撞车的假 ID。
        article_number = legal_raw.get("article_number")
        if article_number is not None:
            metadata["article_id"] = f"{r.doc_id}:{article_number}"

        # 来源标记：命中全局法条库为 global，其余（个人库 / 会话附件）为 personal。
        # 只在解析到全局库时才标注，避免非法条部署凭空多出该字段。
        if global_kb_ids is not None:
            kb_id = chunk_kb.get(r.chunk_id)
            metadata["source"] = "global" if kb_id in global_set else "personal"

        items.append(
            RetrievalResultItem(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                filename=doc_filenames.get(r.doc_id, ""),
                source_type=source_type,
                # content 是 Milvus 的索引字段，带 [法名 第N条] / [文件名] 前缀，
                # 只服务于词法匹配；对外返回前剥离，避免污染法条正文（见 D12）。
                content=strip_content_prefix(r.content),
                child_content=strip_content_prefix(r.child_content or r.content),
                score=round(r.score, 4),
                rrf_score=trace_entry.get("rrf_score"),
                rerank_score=trace_entry.get("rerank_score"),
                routes=trace_entry.get("routes", []),
                metadata=metadata,
            )
        )
    return items
