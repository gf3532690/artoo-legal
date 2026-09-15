import { useMemo, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  Search,
  FileText,
  AlertCircle,
  ChevronDown,
  Minus,
  Plus,
  Clock,
  CornerDownRight,
} from 'lucide-react'
import {
  retrievalApi,
  legalApi,
  knowledgeBaseApi,
  type LegalArticleDetail,
  type RetrievalResultItem,
  type RetrievalTestResponse,
} from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/tooltip'
import RetrievalResultsSkeleton from '@/components/skeletons/RetrievalResultsSkeleton'
import { metaText, metaValidityStatus, validityLabel, validityTone } from '@/lib/legalValidity'

// 法条库类型
interface KnowledgeBaseItem {
  id: string
  name: string
}

// 路由展示元信息：标签、配色（数据色，区别于品牌绿主色）、说明
const ROUTE_META: Record<string, { label: string; cls: string; dot: string; desc: string }> = {
  dense: {
    label: '语义',
    cls: 'text-sky-600 bg-sky-500/10 ring-sky-500/20',
    dot: 'bg-sky-500',
    desc: 'Dense 稠密向量检索：按语义相似度召回',
  },
  sparse: {
    label: '稀疏',
    cls: 'text-violet-600 bg-violet-500/10 ring-violet-500/20',
    dot: 'bg-violet-500',
    desc: 'Sparse 稀疏向量检索：subword 级模糊匹配',
  },
  bm25: {
    label: '关键词',
    cls: 'text-amber-600 bg-amber-500/10 ring-amber-500/20',
    dot: 'bg-amber-500',
    desc: 'BM25 全文检索：精确关键词匹配',
  },
}

const MODES = [
  { value: 'direct', label: '直接检索' },
  { value: 'hybrid', label: '混合检索' },
]

// 检索能力选择：每一项对应一个真实对外接口（页面上的选择器本质就是切换接口）。
// - 关键词检索 / 精确检索 都走 POST /api/retrieval/search，区别是 match_mode；
// - 法条详情走 GET /api/legal/articles/{article_id}，输入的是 article_id 而不是查询词。
const CAPABILITIES = [
  {
    value: 'search' as const,
    label: '关键词检索',
    endpoint: 'POST /api/retrieval/search',
    inputHint: '输入检索查询（关键词 / 法条编号 / 正文片段），回车开始检索…',
    needsKb: true,
  },
  {
    value: 'exact' as const,
    label: '精确检索',
    endpoint: 'POST /api/retrieval/search · match_mode=exact',
    inputHint: '输入「法名 + 条号」，如：民法典第一条',
    needsKb: true,
  },
  {
    value: 'article' as const,
    label: '法条详情',
    endpoint: 'GET /api/legal/articles/{article_id}',
    inputHint: '粘贴检索结果里的 metadata.article_id，如：<doc_id>:146',
    needsKb: false,
  },
]

type Capability = (typeof CAPABILITIES)[number]['value']

// 两种能力返回两种形状，用判别联合让渲染端各自收窄。
type QueryResult =
  | { kind: 'search'; res: RetrievalTestResponse }
  | { kind: 'article'; detail: LegalArticleDetail }

// 检索测试页面（纯检索，不经过 LLM 生成；用于调参与召回质量验证）
function Retrieval() {
  const [capability, setCapability] = useState<Capability>('search')
  const [query, setQuery] = useState('')
  const [selectedKb, setSelectedKb] = useState('')
  const [mode, setMode] = useState('hybrid')
  const [topK, setTopK] = useState(10)
  const [expandedItems, setExpandedItems] = useState<Set<number>>(new Set())

  const activeCapability = CAPABILITIES.find((c) => c.value === capability)!

  // 获取法条库列表
  const { data: knowledgeBases = [] } = useQuery({
    queryKey: ['knowledge-bases'],
    queryFn: () =>
      knowledgeBaseApi.list({ page_size: 100 }).then((res) => res.items as KnowledgeBaseItem[]),
  })

  // 效力状态词表（取值→中文标签）。标签由服务端下发，前端不写死这个枚举。
  const { data: validityOptions } = useQuery({
    queryKey: ['legal-validity-statuses'],
    queryFn: () => legalApi.validityStatuses(),
    staleTime: Infinity,
  })
  const validityLabels = useMemo(() => {
    const map: Record<number, string> = {}
    for (const option of validityOptions ?? []) map[option.value] = option.label
    return Object.keys(map).length > 0 ? map : undefined
  }, [validityOptions])

  // 按能力走各自的真实接口
  const searchMutation = useMutation({
    mutationFn: async (): Promise<QueryResult> => {
      if (capability === 'article') {
        return { kind: 'article', detail: await legalApi.article(query.trim()) }
      }
      return {
        kind: 'search',
        res: await retrievalApi.search({
          query,
          knowledge_base_id: selectedKb,
          // exact 只认「法名 + 条号」，mode / top_k 对它没有意义，直接不传。
          ...(capability === 'exact'
            ? { match_mode: 'exact' as const }
            : { match_mode: 'semantic' as const, mode, top_k: topK }),
        }),
      }
    },
  })

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    if (activeCapability.needsKb && !selectedKb) return
    setExpandedItems(new Set())
    searchMutation.mutate()
  }

  const outcome = searchMutation.data
  const data = outcome?.kind === 'search' ? outcome.res : undefined
  const detail = outcome?.kind === 'article' ? outcome.detail : undefined
  const results: RetrievalResultItem[] = data?.results || []
  const isPending = searchMutation.isPending
  const isError = searchMutation.isError
  const isSuccess = searchMutation.isSuccess

  function toggleExpand(idx: number) {
    setExpandedItems((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div>
        {/* 页面头部 */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold tracking-tight">检索测试</h1>
          <p className="text-muted-foreground text-sm mt-1">
            纯检索测试，不经过大模型生成，用于验证召回质量与调参
          </p>
        </div>

        {/* 检索面板 */}
        <form onSubmit={handleSearch} className="space-y-3 mb-8">
          {/* 能力选择：每一项对应一个真实对外接口 */}
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="inline-flex h-9 items-center rounded-lg bg-muted/60 p-0.5">
              {CAPABILITIES.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  onClick={() => {
                    setCapability(c.value)
                    // 切换能力时清掉上一轮结果，避免把 A 能力的结果当 B 能力的输出看。
                    searchMutation.reset()
                  }}
                  className={`h-8 px-3.5 text-sm rounded-md transition-colors cursor-pointer ${
                    capability === c.value
                      ? 'bg-card text-foreground shadow-sm font-medium'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {c.label}
                </button>
              ))}
            </div>
            <code className="text-[11px] font-mono text-muted-foreground/80">
              {activeCapability.endpoint}
            </code>
          </div>

          {/* 搜索栏 */}
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={activeCapability.inputHint}
              className="h-12 pl-11 pr-28 text-[15px] rounded-xl shadow-sm border-border/70 focus-visible:ring-2 focus-visible:ring-primary/30"
            />
            <Button
              type="submit"
              disabled={!query.trim() || (activeCapability.needsKb && !selectedKb) || isPending}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 h-9 gap-1.5 rounded-lg cursor-pointer"
            >
              {isPending ? (
                <div className="h-3.5 w-3.5 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin" />
              ) : (
                <Search className="h-3.5 w-3.5" />
              )}
              检索
            </Button>
          </div>

          {/* 参数行 */}
          <div className="flex items-center gap-2.5 flex-wrap">
            {/* 法条库 */}
            {activeCapability.needsKb && (
              <Select value={selectedKb} onValueChange={setSelectedKb}>
                <SelectTrigger className="h-9 w-[200px] text-sm rounded-lg bg-card">
                  <SelectValue placeholder="选择法条库" />
                </SelectTrigger>
                <SelectContent>
                  {knowledgeBases.map((kb) => (
                    <SelectItem key={kb.id} value={kb.id}>
                      {kb.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {/* 模式与 Top-K 只对关键词检索有意义：exact 只认「法名 + 条号」，两者都不参与 */}
            {capability === 'search' && (
              <>
                {/* 模式分段控件 */}
                <div className="inline-flex h-9 items-center rounded-lg bg-muted/60 p-0.5">
                  {MODES.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => setMode(m.value)}
                      className={`h-8 px-3.5 text-sm rounded-md transition-colors cursor-pointer ${
                        mode === m.value
                          ? 'bg-card text-foreground shadow-sm font-medium'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>

                {/* Top-K 步进器 */}
                <div className="inline-flex h-9 items-center rounded-lg bg-card border border-border/70 overflow-hidden">
                  <span className="pl-3 pr-2 text-xs text-muted-foreground select-none">Top-K</span>
                  <button
                    type="button"
                    onClick={() => setTopK((v) => Math.max(1, v - 1))}
                    disabled={topK <= 1}
                    className="h-full w-8 flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    aria-label="减少 Top-K"
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </button>
                  <span className="w-8 text-center text-sm font-medium tabular-nums">{topK}</span>
                  <button
                    type="button"
                    onClick={() => setTopK((v) => Math.min(100, v + 1))}
                    disabled={topK >= 100}
                    className="h-full w-8 flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    aria-label="增加 Top-K"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </div>
              </>
            )}
          </div>
        </form>

        {/* 错误提示 */}
        {isError && (
          <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3.5 mb-6">
            <AlertCircle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
            <p className="text-sm text-destructive">
              {searchMutation.error?.message || '检索失败'}
            </p>
          </div>
        )}

        {/* exact 未命中而回退到语义召回时的说明（服务端不静默降级，这里如实展示） */}
        {!isPending && data?.fallback_reason && (
          <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3.5 mb-6">
            <AlertCircle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-sm text-amber-700">
              精确检索未命中，已回退为语义召回：{data.fallback_reason}
            </p>
          </div>
        )}

        {/* 骨架屏 */}
        {isPending && <RetrievalResultsSkeleton count={4} />}

        {/* 法条详情 */}
        {!isPending && detail && <ArticleDetailPanel detail={detail} />}

        {/* 检索链路追踪（仅 hybrid 模式） */}
        {!isPending && !detail && data?.trace && (
          <RetrievalTracePanel trace={data.trace} elapsedMs={data.elapsed_ms} total={data.total} />
        )}

        {/* 结果区头部（direct 模式或无 trace 时） */}
        {!isPending && !detail && results.length > 0 && !data?.trace && (
          <div className="flex items-baseline justify-between mb-4">
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold tabular-nums tracking-tight">{results.length}</span>
              <span className="text-sm text-muted-foreground">条结果</span>
              {data?.has_more && (
                <span className="text-xs text-muted-foreground/80">（候选池中还有更多）</span>
              )}
            </div>
            {data?.elapsed_ms !== undefined && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
                <Clock className="h-3 w-3" />
                {data.elapsed_ms} ms
              </span>
            )}
          </div>
        )}

        {/* 结果列表 */}
        {!isPending && !detail && results.length > 0 && (
          <div className="space-y-2.5 animate-in fade-in-0 duration-500">
            {results.map((result, idx) => (
              <ResultCard
                key={result.chunk_id || idx}
                result={result}
                index={idx}
                isExpanded={expandedItems.has(idx)}
                onToggle={() => toggleExpand(idx)}
                isHybrid={!!data?.trace}
                validityLabels={validityLabels}
              />
            ))}
          </div>
        )}

        {/* 空结果 */}
        {!isPending && isSuccess && !detail && results.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="w-16 h-16 rounded-2xl bg-muted/60 flex items-center justify-center mb-4">
              <Search className="h-7 w-7 text-muted-foreground/60" />
            </div>
            <p className="text-muted-foreground">未找到相关结果</p>
            <p className="text-xs text-muted-foreground/70 mt-1">尝试调整查询内容或切换检索模式</p>
          </div>
        )}

        {/* 初始状态 */}
        {!isSuccess && !isError && !isPending && (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="w-16 h-16 rounded-2xl bg-primary/5 flex items-center justify-center mb-4">
              <Search className="h-7 w-7 text-primary/40" />
            </div>
            <p className="text-sm text-muted-foreground">选择法条库并输入查询内容开始检索</p>
          </div>
        )}
      </div>
    </TooltipProvider>
  )
}

// ============================================================
// 单条结果卡片
// ============================================================

// 法条详情（GET /api/legal/articles/{article_id}）：条文全文 + 元数据。
// 不展示修订历史与关联司法解释——语料里没有这两类数据，接口也不返回。
function ArticleDetailPanel({ detail }: { detail: LegalArticleDetail }) {
  const chips = [
    detail.law_type,
    detail.province && (detail.city ? `${detail.province} / ${detail.city}` : detail.province),
    detail.source === 'global' ? '全局法条库' : detail.source === 'personal' ? '个人库' : null,
  ].filter(Boolean) as string[]

  const meta: [string, string][] = ([
    ['发布机关', detail.issuing_authority],
    ['发布日期', detail.publish_date],
    ['施行日期', detail.effective_date],
    ['时效状态', detail.validity_status === null ? null : `validity_status = ${detail.validity_status}`],
    ['来源文件', detail.filename],
    ['article_id', detail.article_id],
  ] as [string, string | null][]).filter(([, v]) => !!v) as [string, string][]

  return (
    <div className="rounded-xl border border-border/60 bg-card shadow-sm animate-in fade-in-0 duration-500">
      <div className="p-4">
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium text-foreground/90">
            {detail.law_name || detail.filename}
          </span>
          {detail.article_label && (
            <span className="text-sm font-semibold text-primary">{detail.article_label}</span>
          )}
          {chips.map((chip) => (
            <span
              key={chip}
              className="inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium ring-1 text-muted-foreground bg-muted/40 ring-border/60"
            >
              {chip}
            </span>
          ))}
        </div>

        <p className="text-[15px] leading-7 whitespace-pre-wrap text-foreground/90">
          {detail.content}
        </p>

        <div className="mt-4 pt-3 border-t border-border/60 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5">
          {meta.map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-2 text-xs min-w-0">
              <span className="text-muted-foreground/70 shrink-0">{k}</span>
              <span className="text-foreground/80 truncate">{v}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function ResultCard({
  result,
  index,
  isExpanded,
  onToggle,
  isHybrid,
  validityLabels,
}: {
  result: RetrievalResultItem
  index: number
  isExpanded: boolean
  onToggle: () => void
  isHybrid: boolean
  validityLabels?: Record<number, string>
}) {
  const hasParent =
    !!result.content && !!result.child_content && result.content !== result.child_content

  // 法条身份与时间。同名法、同条号在不同法/不同版本里都有，光看正文分不出来，
  // 这里把法名、条号、公布/施行日期和效力状态一起摆出来。
  const lawName = metaText(result.metadata, 'law_name')
  const articleLabel = metaText(result.metadata, 'article_label')
  const publishDate = metaText(result.metadata, 'publish_date')
  const effectiveDate = metaText(result.metadata, 'effective_date')
  const validityStatus = metaValidityStatus(result.metadata)
  const validity = validityLabel(validityStatus, validityLabels)
  const hasLegalIdentity =
    !!lawName || !!articleLabel || !!publishDate || !!effectiveDate || !!validity

  // 最终分数配色（语义化：高/中/低）
  function scoreColor(score: number) {
    if (score >= 0.8) return 'text-emerald-600'
    if (score >= 0.5) return 'text-amber-600'
    return 'text-rose-500'
  }

  return (
    <div className="group rounded-xl border border-border/60 bg-card shadow-sm transition-all hover:border-border hover:shadow-md">
      <div className="p-4">
        {/* 顶部行：序号 + 文件名 + 路由 + 分数 */}
        <div className="flex items-center justify-between gap-3 mb-2.5">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-xs font-medium text-muted-foreground/70 tabular-nums shrink-0">
              {String(index + 1).padStart(2, '0')}
            </span>
            <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            <span className="text-sm font-medium truncate text-foreground/90">
              {result.filename || result.doc_id?.slice(0, 12)}
            </span>
            {/* 命中路由 */}
            <div className="flex items-center gap-1 shrink-0">
              {result.routes.map((route) => {
                const meta = ROUTE_META[route]
                if (!meta) return null
                return (
                  <Tooltip key={route}>
                    <TooltipTrigger asChild>
                      <span
                        className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ring-1 cursor-default ${meta.cls}`}
                      >
                        <span className={`h-1 w-1 rounded-full ${meta.dot}`} />
                        {meta.label}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>{meta.desc}</TooltipContent>
                  </Tooltip>
                )
              })}
            </div>
          </div>

          {/* 分数：最终分为主，rrf/rerank 为辅 */}
          <div className="flex items-center gap-3 shrink-0">
            {isHybrid && (
              <div className="hidden sm:flex items-center gap-2.5 text-[11px] font-mono text-muted-foreground/70 tabular-nums">
                <SubScore label="RRF" value={result.rrf_score} hint="RRF 三路融合分数" />
                <SubScore label="RR" value={result.rerank_score} hint="Rerank 精排分数" />
              </div>
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className={`text-base font-semibold font-mono tabular-nums cursor-default ${scoreColor(result.score)}`}
                >
                  {result.score?.toFixed(3)}
                </span>
              </TooltipTrigger>
              <TooltipContent>{isHybrid ? '综合分数（composite）' : '语义相似度分数'}</TooltipContent>
            </Tooltip>
          </div>
        </div>

        {/* 法条身份与时间 */}
        {hasLegalIdentity && (
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 mb-2.5 text-xs text-muted-foreground">
            {lawName && <span className="font-medium text-foreground/80">{lawName}</span>}
            {articleLabel && <span className="font-medium text-primary">{articleLabel}</span>}
            {publishDate && (
              <span title="公布日期：这个版本的公布日。判断哪一条更新，比它">
                公布 {publishDate}
              </span>
            )}
            {effectiveDate && (
              <span title="施行日期：这个版本开始生效的日期。问「行为时法」用它">
                施行 {effectiveDate}
              </span>
            )}
            {validity && (
              <span
                className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${validityTone(
                  validityStatus
                )}`}
              >
                {validity}
              </span>
            )}
          </div>
        )}

        {/* 命中内容（子块） */}
        <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/80">
          {result.child_content || result.content}
        </p>
      </div>

      {/* 展开父块 */}
      {hasParent && (
        <div className="border-t border-border/50">
          <button
            type="button"
            onClick={onToggle}
            className="flex items-center gap-1.5 px-4 py-2 text-xs text-muted-foreground hover:text-foreground transition-colors w-full text-left cursor-pointer"
          >
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${isExpanded ? '' : '-rotate-90'}`} />
            {isExpanded ? '收起完整上下文' : '查看完整上下文（父块）'}
          </button>
          {isExpanded && (
            <div className="px-4 pb-4">
              <div className="rounded-lg bg-muted/40 p-3.5 text-sm leading-relaxed whitespace-pre-wrap text-foreground/75 border-l-2 border-primary/40">
                {highlightChild(result.content, result.child_content)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// 辅助分数（RRF / Rerank），值为空时不渲染
function SubScore({ label, value, hint }: { label: string; value: number | null; hint: string }) {
  if (value === null || value === undefined) return null
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-default">
          <span className="text-muted-foreground/50">{label}</span> {value.toFixed(3)}
        </span>
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  )
}

// 在父块内容中高亮子块命中部分
function highlightChild(parentContent: string, childContent: string) {
  if (!childContent || !parentContent.includes(childContent)) {
    return <span>{parentContent}</span>
  }
  const idx = parentContent.indexOf(childContent)
  return (
    <>
      {parentContent.slice(0, idx) && <span>{parentContent.slice(0, idx)}</span>}
      <mark className="bg-primary/15 text-foreground font-medium rounded-sm px-0.5">
        {parentContent.slice(idx, idx + childContent.length)}
      </mark>
      {parentContent.slice(idx + childContent.length) && (
        <span>{parentContent.slice(idx + childContent.length)}</span>
      )}
    </>
  )
}

// ============================================================
// 检索链路追踪面板：召回统计 + 处理漏斗
// ============================================================

function RetrievalTracePanel({
  trace,
  elapsedMs,
  total,
}: {
  trace: NonNullable<RetrievalTestResponse['trace']>
  elapsedMs: number
  total: number
}) {
  const maxFunnel = Math.max(...trace.funnel.map((f) => f.count), 1)

  return (
    <div className="mb-8">
      {/* 概览头：KPI 风格，无边框，靠层级区分 */}
      <div className="flex items-baseline justify-between mb-4">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold tabular-nums tracking-tight">{total}</span>
          <span className="text-sm text-muted-foreground">条结果</span>
          <span className="text-muted-foreground/40 mx-1">·</span>
          <span className="text-xs text-muted-foreground">检索链路</span>
        </div>
        <span className="flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
          <Clock className="h-3 w-3" />
          {elapsedMs} ms
        </span>
      </div>

      {/* 链路卡片 */}
      <div className="rounded-xl border border-border/60 bg-card shadow-sm p-5">
        <div className="grid grid-cols-1 md:grid-cols-[180px_1fr] gap-x-8 gap-y-5">
          {/* 三路召回 */}
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60 mb-3">
              三路召回
            </div>
            <div className="space-y-2.5">
              {trace.routes.map((route) => {
                const meta = ROUTE_META[route.name]
                const disabled = route.enabled === false
                return (
                  <div key={route.name} className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 text-sm">
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${meta?.dot || 'bg-muted-foreground'} ${disabled ? 'opacity-30' : ''}`}
                      />
                      <span className={disabled ? 'text-muted-foreground/50' : 'text-foreground/80'}>
                        {meta?.label || route.name}
                      </span>
                    </span>
                    <span
                      className={`text-sm font-mono tabular-nums ${disabled ? 'text-muted-foreground/40' : 'text-foreground/70'}`}
                    >
                      {disabled ? '—' : route.recalled}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>

          {/* 处理漏斗 */}
          <div className="md:border-l md:border-border/50 md:pl-8">
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60 mb-3">
              处理漏斗
            </div>
            <div className="space-y-2">
              {trace.funnel.map((stage) => (
                <div key={stage.stage} className="flex items-center gap-3">
                  <span className="text-xs text-muted-foreground w-20 shrink-0 truncate">
                    {stage.stage}
                  </span>
                  <div className="flex-1 h-1.5 bg-muted/50 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary/70 rounded-full transition-all duration-500"
                      style={{ width: `${Math.max((stage.count / maxFunnel) * 100, 2)}%` }}
                    />
                  </div>
                  <span className="text-xs font-mono tabular-nums text-foreground/70 w-9 text-right shrink-0">
                    {stage.count}
                  </span>
                </div>
              ))}
            </div>
            {/* 最终输出提示 */}
            <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border/40 text-xs text-muted-foreground">
              <CornerDownRight className="h-3 w-3" />
              最终返回 <span className="font-mono tabular-nums text-foreground/80">{total}</span> 条
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default Retrieval
