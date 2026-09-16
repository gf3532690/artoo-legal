// 检索测试页的接口契约测试。
//
// 钉住一件事：页面上切换的每个"检索能力"必须打到**真实对外接口**，而不是各自的影子路径——
//   · 关键词检索 → POST /api/retrieval/search（match_mode=semantic，带 mode/top_k）
//   · 精确检索   → POST /api/retrieval/search（match_mode=exact，不带 mode/top_k）
//   · 法条详情   → GET  /api/legal/articles/{article_id}
// 页面是调参与对接自测入口，它打的接口和第三方拿到的必须一致，否则验收结论不可信。

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createElement, type ReactNode } from 'react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { knowledgeBaseApi, legalApi, retrievalApi } from '@/lib/api'

import Retrieval from './Retrieval'

vi.mock('@/lib/api', () => ({
  retrievalApi: { search: vi.fn() },
  legalApi: { article: vi.fn(), validityStatuses: vi.fn() },
  knowledgeBaseApi: { list: vi.fn() },
}))

const mockedSearch = vi.mocked(retrievalApi.search)
const mockedArticle = vi.mocked(legalApi.article)
const mockedValidity = vi.mocked(legalApi.validityStatuses)
const mockedList = vi.mocked(knowledgeBaseApi.list)

// jsdom 缺 Radix Select 用到的两个指针 API。
beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
  Element.prototype.scrollIntoView = vi.fn()
})

function emptySearchResponse() {
  return {
    query: 'q',
    mode: 'hybrid',
    total: 0,
    elapsed_ms: 1,
    results: [],
    trace: null,
    degraded: false,
    failed_source_count: 0,
    page: 1,
    page_size: 10,
    has_more: false,
    match_mode: 'semantic',
    fallback_reason: null,
  }
}

// 一条带完整法条元数据的结果：用来钉住"检索结果自己就带时间"。
function searchResponseWithLegalMeta() {
  return {
    ...emptySearchResponse(),
    total: 1,
    results: [
      {
        chunk_id: 'c1',
        doc_id: 'd1',
        filename: '中华人民共和国民法典.docx',
        content: '第一条 为了保护民事主体的合法权益，调整民事关系，根据宪法，制定本法。',
        child_content: '第一条 为了保护民事主体的合法权益，调整民事关系，根据宪法，制定本法。',
        score: 0.9,
        rrf_score: null,
        rerank_score: null,
        routes: ['dense'],
        metadata: {
          law_name: '中华人民共和国民法典',
          article_label: '第一条',
          article_id: 'd1:1',
          publish_date: '2020-05-28',
          effective_date: '2021-01-01',
          validity_status: 3,
        },
      },
    ],
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return createElement(QueryClientProvider, { client }, children)
}

async function selectKb(user: ReturnType<typeof userEvent.setup>) {
  // 库选择器是多选下拉（DropdownMenuCheckboxItem，role=menuitemcheckbox）。勾选不会自动
  // 关菜单（故意的，方便连选），所以这里手动 Esc 收起来，免得挡住后面的「检索」按钮。
  await user.click(screen.getByRole('button', { name: /选择法条库/ }))
  await user.click(await screen.findByRole('menuitemcheckbox', { name: '全局法条库' }))
  await user.keyboard('{Escape}')
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedList.mockResolvedValue({ items: [{ id: 'kb-1', name: '全局法条库' }], total: 1 } as never)
  mockedSearch.mockResolvedValue(emptySearchResponse() as never)
  mockedValidity.mockResolvedValue([
    { value: 3, label: '现行有效' },
    { value: 2, label: '已修改' },
    { value: 1, label: '已废止' },
    { value: -1, label: '已失效' },
    { value: 4, label: '尚未生效' },
    { value: 0, label: '未标注' },
  ] as never)
})

describe('检索测试页的能力切换', () => {
  it('关键词检索：打到 /retrieval/search，match_mode=semantic 且带 mode/top_k', async () => {
    const user = userEvent.setup()
    render(createElement(Retrieval), { wrapper })

    await selectKb(user)
    await user.type(screen.getByPlaceholderText(/输入检索查询/), '燃放烟花爆竹')
    await user.click(screen.getByRole('button', { name: '检索' }))

    expect(mockedSearch).toHaveBeenCalledTimes(1)
    expect(mockedSearch.mock.calls[0][0]).toEqual({
      query: '燃放烟花爆竹',
      kb_ids: ['kb-1'],
      match_mode: 'semantic',
      mode: 'hybrid',
      top_k: 10,
    })
    expect(mockedArticle).not.toHaveBeenCalled()
  })

  it('精确检索：同样打到 /retrieval/search，但只传 match_mode=exact', async () => {
    const user = userEvent.setup()
    render(createElement(Retrieval), { wrapper })

    // 精确检索不需要选库之外的模式/Top-K，界面也不再显示它们
    await user.click(screen.getByRole('button', { name: '精确检索' }))
    expect(screen.queryByText('Top-K')).toBeNull()
    expect(screen.getByText('POST /api/retrieval/search · match_mode=exact')).toBeInTheDocument()

    await selectKb(user)
    await user.type(screen.getByPlaceholderText(/法名 \+ 条号/), '民法典第一条')
    await user.click(screen.getByRole('button', { name: '检索' }))

    expect(mockedSearch).toHaveBeenCalledTimes(1)
    expect(mockedSearch.mock.calls[0][0]).toEqual({
      query: '民法典第一条',
      kb_ids: ['kb-1'],
      match_mode: 'exact',
    })
  })

  it('法条详情：改打 GET /legal/articles/{article_id}，且不要求选库', async () => {
    const user = userEvent.setup()
    mockedArticle.mockResolvedValue({
      article_id: 'doc-1:146',
      law_name: '中华人民共和国民法典',
      article_label: '第一百四十六条',
      content: '行为人与相对人以虚假的意思表示实施的民事法律行为无效。',
    } as never)
    render(createElement(Retrieval), { wrapper })

    await user.click(screen.getByRole('button', { name: '法条详情' }))
    expect(screen.getByText('GET /api/legal/articles/{article_id}')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText(/article_id/), 'doc-1:146')
    await user.click(screen.getByRole('button', { name: '检索' }))

    expect(mockedArticle).toHaveBeenCalledWith('doc-1:146')
    expect(mockedSearch).not.toHaveBeenCalled()
    expect(await screen.findByText('第一百四十六条')).toBeInTheDocument()
    expect(screen.getByText(/行为人与相对人以虚假的意思表示/)).toBeInTheDocument()
  })

  it('检索结果自带法名、条号、公布/施行日期与效力状态', async () => {
    // 同一部法的新旧版本、以及针对某条的补充文件，正文看起来都像"第一条"，光看
    // 内容分不出谁新谁旧。这两条日期就是给调用方作参考的，必须落在结果卡片上。
    mockedSearch.mockResolvedValue(searchResponseWithLegalMeta() as never)
    const user = userEvent.setup()
    render(createElement(Retrieval), { wrapper })

    await selectKb(user)
    await user.type(screen.getByPlaceholderText(/输入检索查询/), '民法典第一条')
    await user.click(screen.getByRole('button', { name: '检索' }))

    expect(await screen.findByText('中华人民共和国民法典')).toBeInTheDocument()
    expect(screen.getByText('第一条')).toBeInTheDocument()
    expect(screen.getByText(/公布 2020-05-28/)).toBeInTheDocument()
    expect(screen.getByText(/施行 2021-01-01/)).toBeInTheDocument()
    expect(screen.getByText('现行有效')).toBeInTheDocument()
  })

  it('结果里的 article_id 点一下就用法条详情能力查它', async () => {
    // 这两个能力返回的 metadata.article_id 就是详情接口的入参；页面上直接给出来，
    // 否则想查详情只能去翻原始响应。
    mockedSearch.mockResolvedValue(searchResponseWithLegalMeta() as never)
    mockedArticle.mockResolvedValue({
      article_id: 'd1:1',
      law_name: '中华人民共和国民法典',
      article_label: '第一条',
      content: '为了保护民事主体的合法权益，调整民事关系，根据宪法，制定本法。',
    } as never)
    const user = userEvent.setup()
    render(createElement(Retrieval), { wrapper })

    await selectKb(user)
    await user.type(screen.getByPlaceholderText(/输入检索查询/), '民法典第一条')
    await user.click(screen.getByRole('button', { name: '检索' }))

    await user.click(await screen.findByTitle('点一下用法条详情接口查这一条'))

    expect(mockedArticle).toHaveBeenCalledWith('d1:1')
    expect(await screen.findByText(/为了保护民事主体的合法权益/)).toBeInTheDocument()
  })

  it('法条库支持多选：勾两个就按两个库联合检索', async () => {
    // 多库走接口的 kb_ids（服务端据此走多源联合召回）。单选时也发这个字段，
    // 所以这里同时钉住"选一个"与"选两个"的入参形状一致。
    mockedList.mockResolvedValue({
      items: [
        { id: 'kb-1', name: '全局法条库' },
        { id: 'kb-2', name: '个人法条库' },
      ],
      total: 2,
    } as never)
    const user = userEvent.setup()
    render(createElement(Retrieval), { wrapper })

    await user.click(screen.getByRole('button', { name: /选择法条库/ }))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: '全局法条库' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: '个人法条库' }))
    await user.keyboard('{Escape}')

    // 两个都勾上时触发器显示数量，而不是某一个的名字
    expect(screen.getByRole('button', { name: /已选 2 个库/ })).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText(/输入检索查询/), '燃放烟花爆竹')
    await user.click(screen.getByRole('button', { name: '检索' }))

    expect(mockedSearch.mock.calls[0][0]).toMatchObject({ kb_ids: ['kb-1', 'kb-2'] })
  })

  it('多选时检索方式不可选：多源固定走混合召回', async () => {
    mockedList.mockResolvedValue({
      items: [
        { id: 'kb-1', name: '全局法条库', config: { is_default_legal_kb: true } },
        { id: 'kb-2', name: '个人法条库', config: {} },
      ],
      total: 2,
    } as never)
    const user = userEvent.setup()
    render(createElement(Retrieval), { wrapper })

    await user.click(screen.getByRole('button', { name: /选择法条库/ }))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: '全局法条库' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: '个人法条库' }))
    await user.keyboard('{Escape}')

    expect(screen.getByRole('button', { name: '直接检索' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '混合检索' })).toBeDisabled()
    expect(screen.getByText('多源固定混合')).toBeInTheDocument()
  })

  it('只勾全局法条库时检索方式仍可选（服务端合并后只有一个源）', async () => {
    mockedList.mockResolvedValue({
      items: [
        { id: 'kb-1', name: '全局法条库', config: { is_default_legal_kb: true } },
        { id: 'kb-2', name: '个人法条库', config: {} },
      ],
      total: 2,
    } as never)
    const user = userEvent.setup()
    render(createElement(Retrieval), { wrapper })

    await user.click(screen.getByRole('button', { name: /选择法条库/ }))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: '全局法条库' }))
    await user.keyboard('{Escape}')

    expect(screen.getByRole('button', { name: '直接检索' })).toBeEnabled()
  })
})
