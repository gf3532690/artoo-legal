import { useQuery } from '@tanstack/react-query'

import { knowledgeBaseApi } from '@/lib/api'
import Documents from './Documents'

interface GlobalLegalKb {
  id: string
  name: string
}

/**
 * 法条库入口页（路由 `/legal`）。
 *
 * 本部署是单租户部署，全局法条库全租户只有一份。本页只做一件事：把它解析出来，
 * 然后直接渲染法条库内容维护页。这样左侧菜单点一次就落在内容维护上，
 * 不必先经过「法条库列表 → 选库」两步。
 *
 * 解析失败（引导未完成 / 无权限）时给出空态提示，而不是白屏。
 */
export default function LegalLibrary() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['global-legal-kb'],
    queryFn: () => knowledgeBaseApi.getGlobalLegal() as Promise<GlobalLegalKb>,
    staleTime: 60_000,
  })

  if (isLoading) {
    return (
      <div className="p-6 text-sm text-muted-foreground">正在加载法条库…</div>
    )
  }

  if (error || !data?.id) {
    return (
      <div className="space-y-2 p-6">
        <p className="text-sm text-muted-foreground">未找到全局法条库。</p>
        <p className="text-xs text-muted-foreground">
          请确认部署已完成引导（默认租户 + 全局法条库），或联系管理员。
        </p>
      </div>
    )
  }

  return <Documents explicitKbId={data.id} />
}
