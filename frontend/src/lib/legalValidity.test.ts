// 法条时间/效力状态的展示辅助。
//
// 这里钉的是**后端缺值约定**：元数据取不到的字段是「整键缺失」而不是 `null`，所以从
// 检索结果里读字段必须容忍键不存在、值不是字符串/整数。读错的表现是列表上凭空多出
// 一行空白，或者把 `undefined` 渲染成文字。

import { describe, expect, it } from 'vitest'

import { metaText, metaValidityStatus, validityLabel, validityTone } from './legalValidity'

const LABELS: Record<number, string> = { 3: '现行有效', 1: '已废止', [-1]: '已失效', 0: '未标注' }

describe('validityLabel', () => {
  it('按服务端词表渲染', () => {
    expect(validityLabel(3, LABELS)).toBe('现行有效')
  })

  it('0 是「未标注」而不是「没有值」', () => {
    expect(validityLabel(0, LABELS)).toBe('未标注')
  })

  it('没有词表、没有值时都不渲染', () => {
    expect(validityLabel(3, undefined)).toBeNull()
    expect(validityLabel(null, LABELS)).toBeNull()
  })

  it('词表外的取值如实回显，不编造含义', () => {
    expect(validityLabel(7, LABELS)).toBe('取值 7')
  })
})

describe('validityTone', () => {
  it('已知取值有各自配色', () => {
    expect(validityTone(3)).not.toBe(validityTone(1))
  })

  it('未知取值与空值退到中性灰', () => {
    expect(validityTone(999)).toBe(validityTone(null))
    expect(validityTone(undefined)).toBe(validityTone(null))
  })
})

describe('metaText', () => {
  it('读到字符串就返回', () => {
    expect(metaText({ publish_date: '2020-05-28' }, 'publish_date')).toBe('2020-05-28')
  })

  it('整键缺失返回 null', () => {
    expect(metaText({}, 'publish_date')).toBeNull()
    expect(metaText(undefined, 'publish_date')).toBeNull()
  })

  it('空串不算有值', () => {
    // province 在国家层面法规上是 ""，不该当作有内容渲染出来
    expect(metaText({ province: '' }, 'province')).toBeNull()
  })

  it('非字符串不当作值', () => {
    expect(metaText({ publish_date: 20200528 }, 'publish_date')).toBeNull()
    expect(metaText({ publish_date: null }, 'publish_date')).toBeNull()
  })
})

describe('metaValidityStatus', () => {
  it('读到整数就返回，含 0', () => {
    expect(metaValidityStatus({ validity_status: 3 })).toBe(3)
    expect(metaValidityStatus({ validity_status: 0 })).toBe(0)
    expect(metaValidityStatus({ validity_status: -1 })).toBe(-1)
  })

  it('缺失或非数字返回 null', () => {
    expect(metaValidityStatus({})).toBeNull()
    expect(metaValidityStatus(undefined)).toBeNull()
    expect(metaValidityStatus({ validity_status: '3' })).toBeNull()
  })
})
