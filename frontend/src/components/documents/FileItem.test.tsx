// 法条库文件卡片上的「效力状态」徽标。
//
// 这里钉两件事：
// 1. `0`（未标注）是**合法取值**，不能被当成"没有值"而不渲染——它是 2,031 份文档的
//    真实状态，漏掉就等于这些文档在列表里看起来没解析完。
// 2. 标签来自服务端下发的词表；词表没到位时不猜、不渲染，而不是拿数字糊一个中文。

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import FileItem, { validityLabel, type MergedFile } from './FileItem'

const LABELS: Record<number, string> = {
  3: '现行有效',
  2: '已修改',
  1: '已废止',
  [-1]: '已失效',
  4: '尚未生效',
  0: '未标注',
}

function makeDoc(overrides: Partial<MergedFile> = {}): MergedFile {
  return {
    id: 'd1',
    filename: '中华人民共和国民法典.docx',
    file_type: 'docx',
    file_size: 2048,
    status: 'completed',
    error_message: null,
    chunk_count: 10,
    progress: 100,
    progress_message: null,
    isLocal: false,
    law_name: '中华人民共和国民法典',
    validity_status: 3,
    ...overrides,
  }
}

describe('validityLabel', () => {
  it('词表里有什么就显示什么', () => {
    expect(validityLabel(1, LABELS)).toBe('已废止')
    expect(validityLabel(-1, LABELS)).toBe('已失效')
  })

  it('0 是「未标注」，不是「没有值」', () => {
    expect(validityLabel(0, LABELS)).toBe('未标注')
  })

  it('没有词表时返回 null（宁可不显示，也不猜）', () => {
    expect(validityLabel(1, undefined)).toBeNull()
    expect(validityLabel(null, LABELS)).toBeNull()
    expect(validityLabel(undefined, LABELS)).toBeNull()
  })

  it('词表里没有的取值如实回显，不编造含义', () => {
    expect(validityLabel(99, LABELS)).toBe('取值 99')
  })
})

describe('FileItem 效力状态徽标', () => {
  it('给出词表时渲染状态徽标', () => {
    render(
      <FileItem
        doc={makeDoc({ validity_status: 1 })}
        isSelected={false}
        onSelect={vi.fn()}
        validityLabels={LABELS}
      />
    )
    expect(screen.getByText('已废止')).toBeInTheDocument()
  })

  it('未标注（0）同样渲染', () => {
    render(
      <FileItem
        doc={makeDoc({ validity_status: 0 })}
        isSelected={false}
        onSelect={vi.fn()}
        validityLabels={LABELS}
      />
    )
    expect(screen.getByText('未标注')).toBeInTheDocument()
  })

  it('非法条文档（状态为空）不渲染徽标', () => {
    render(
      <FileItem
        doc={makeDoc({ validity_status: null, law_name: null })}
        isSelected={false}
        onSelect={vi.fn()}
        validityLabels={LABELS}
      />
    )
    expect(screen.queryByText('现行有效')).not.toBeInTheDocument()
    expect(screen.queryByText('未标注')).not.toBeInTheDocument()
  })

  it('词表未就绪时不渲染徽标，也不显示裸数字', () => {
    render(
      <FileItem
        doc={makeDoc({ validity_status: 3 })}
        isSelected={false}
        onSelect={vi.fn()}
      />
    )
    expect(screen.queryByText('现行有效')).not.toBeInTheDocument()
    expect(screen.queryByText('3')).not.toBeInTheDocument()
  })
})
