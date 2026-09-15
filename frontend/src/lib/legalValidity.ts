// 法条效力状态与时间的展示辅助（法条库各页面共用）。
//
// **标签不在这里**：它由服务端 `GET /api/legal/validity-statuses` 下发。这个枚举曾经被
// 读反过一次——`0` 是「未标注」、`-1` 才是「已失效」——多一份前端拷贝就多一次抄错的
// 机会。这里只决定配色与"什么时候不显示"。

/** 效力状态徽标配色。未知取值与空值都落到中性灰。 */
const VALIDITY_TONE: Record<number, string> = {
  3: 'bg-green-100 text-green-700 border-green-200', // 现行有效
  2: 'bg-yellow-100 text-yellow-700 border-yellow-200', // 已修改
  4: 'bg-blue-100 text-blue-700 border-blue-200', // 尚未生效
  0: 'bg-muted text-muted-foreground border-border', // 未标注
  1: 'bg-red-100 text-red-700 border-red-200', // 已废止
  [-1]: 'bg-red-50 text-red-600 border-red-200', // 已失效
}

const NEUTRAL_TONE = 'bg-muted text-muted-foreground border-border'

export function validityTone(value: number | null | undefined): string {
  if (value == null) return NEUTRAL_TONE
  return VALIDITY_TONE[value] ?? NEUTRAL_TONE
}

/**
 * 效力状态徽标文案。
 *
 * 拿不到词表时返回 `null`——宁可不显示，也不猜一个含义，更不显示一个裸数字。
 * 词表里没有的取值如实回显 `取值 N`：这样"服务端多了个新状态"会显式暴露出来，
 * 而不是被悄悄渲染成某个旧含义。
 */
export function validityLabel(
  value: number | null | undefined,
  labels?: Record<number, string>
): string | null {
  if (value == null || !labels) return null
  return labels[value] ?? `取值 ${value}`
}

/**
 * 从检索结果的 `metadata` 里读一个字段。
 *
 * 后端对缺失值是**整键缺失**而不是发 `null`，所以这里统一按"字符串才有值"处理。
 */
export function metaText(
  meta: Record<string, unknown> | undefined,
  key: string
): string | null {
  const value = meta?.[key]
  return typeof value === 'string' && value.length > 0 ? value : null
}

/** 从 `metadata` 里读效力状态原值。读不到或不是整数时返回 null。 */
export function metaValidityStatus(
  meta: Record<string, unknown> | undefined
): number | null {
  const value = meta?.['validity_status']
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
