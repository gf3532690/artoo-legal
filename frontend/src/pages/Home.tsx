import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Building2,
  Database,
  Key,
  Layers,
  ScrollText,
  Search,
  Users as UsersIcon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuth } from '@/lib/auth-context'

/**
 * 首页（路由 `/home`）：登录后的默认落地页。
 *
 * 为什么不用「全局法条库」当落地页：那是**维护动作**（上传/删除法条），
 * 而登录后的第一件事大概率不是改语料。落地页只负责回答「这个系统是什么、
 * 我这一步能去哪儿」，把维护页留给用户自己点进去。
 *
 * 入口卡片按角色过滤，与左侧菜单同源（角色 → 可见入口），不额外引入权限模型；
 * 超管是纯平台装配身份、不参与内容，因此看不到「全局法条库」。
 */

interface HomeEntry {
  to: string
  title: string
  description: string
  icon: LucideIcon
  /** 该入口对当前身份是否可见 */
  visible: boolean
}

export default function Home() {
  const { isSuperAdmin, isAdmin, profile } = useAuth()

  const entries: HomeEntry[] = [
    {
      to: '/legal',
      title: '全局法条库',
      description: '维护全平台共用的法条语料：上传新法条、删除被修订的旧文件。',
      icon: Database,
      // 内容维护由租户管理员负责；超管是纯平台身份，不参与内容
      visible: isAdmin,
    },
    {
      to: '/retrieval',
      title: '检索测试',
      description: '输入一句查询，查看条文级召回结果与各路的链路追踪。',
      icon: Search,
      visible: isSuperAdmin,
    },
    {
      to: '/embed-config',
      title: 'Embedding',
      description: '配置向量化与精排（rerank）服务地址、模型与凭据。',
      icon: Layers,
      visible: isSuperAdmin,
    },
    {
      to: '/api-keys',
      title: 'API Key',
      description: '为下游系统签发调用检索接口所需的凭据。',
      icon: Key,
      visible: isSuperAdmin,
    },
    {
      to: '/tenants',
      title: '租户管理',
      description: '平台租户与租户管理员的装配。',
      icon: Building2,
      visible: isSuperAdmin,
    },
    {
      to: '/users',
      title: '用户管理',
      description: '创建与停用本租户账号。',
      icon: UsersIcon,
      visible: isAdmin,
    },
    {
      to: '/audit-logs',
      title: '审计日志',
      description: '查看登录、维护等关键操作的记录。',
      icon: ScrollText,
      visible: isAdmin || isSuperAdmin,
    },
  ]

  const visibleEntries = entries.filter((entry) => entry.visible)

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="space-y-1.5">
        <h1 className="text-xl font-semibold tracking-tight">法条库 · 法条召回服务</h1>
        <p className="text-sm text-muted-foreground">
          {profile?.username ? `${profile.username}，` : ''}
          本部署只做检索：法条入库时被结构化成「法名 + 条号」，检索时返回可定位到具体条文的依据。
          从左侧菜单或下面的入口开始。
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {visibleEntries.map((entry) => (
          <Link key={entry.to} to={entry.to} className="group">
            <Card className="h-full transition-colors group-hover:border-primary/40">
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <entry.icon className="h-4 w-4 text-primary shrink-0" />
                  {entry.title}
                  <ArrowRight className="ml-auto h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </CardTitle>
                <CardDescription className="pt-1.5 text-xs leading-relaxed">
                  {entry.description}
                </CardDescription>
              </CardHeader>
            </Card>
          </Link>
        ))}
      </div>

      <Card>
        <CardContent className="space-y-2 pt-6">
          <p className="text-sm font-medium">对外检索接口</p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            <code className="rounded bg-muted px-1 py-0.5">POST /api/retrieval/search</code>
            {' '}——调用方只需传 <code className="rounded bg-muted px-1 py-0.5">query</code> 与自己的
            <code className="mx-1 rounded bg-muted px-1 py-0.5">kb_ids</code>，
            全局法条库由服务端默认并入；<code className="mx-1 rounded bg-muted px-1 py-0.5">top_k</code>
            不传时为 5。维护全局库后无需额外操作，下一次检索即生效。
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
