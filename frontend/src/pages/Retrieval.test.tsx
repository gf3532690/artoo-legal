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
  legalApi: { article: vi.fn() },
  knowledgeBaseApi: { list: vi.fn() },
}))

const mockedSearch = vi.mocked(retrievalApi.search)
const mockedArticle = vi.mocked(legalApi.article)
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

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return createElement(QueryClientProvider, { client }, children)
}

async function selectKb(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('combobox'))
  await user.click(await screen.findByRole('option', { name: '全局法条库' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedList.mockResolvedValue({ items: [{ id: 'kb-1', name: '全局法条库' }], total: 1 } as never)
  mockedSearch.mockResolvedValue(emptySearchResponse() as never)
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
      knowledge_base_id: 'kb-1',
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
      knowledge_base_id: 'kb-1',
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
})
