import { useState, useEffect } from 'react'
import { NavLink, Outlet, useLocation, useNavigate, Navigate } from 'react-router-dom'
import {
  Database,
  Search,
  Key,
  Settings,
  Layers,
  PanelLeft,
  LogOut,
  KeyRound,
  Building2,
  Users as UsersIcon,
  ScrollText,
  ChevronUp,
  UserCircle,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth-context'
import ProfileDialog from '@/components/ProfileDialog'
import SettingsDialog from '@/components/SettingsDialog'
import ArtifactPanel from '@/components/artifact/ArtifactPanel'
import { useArtifactStore } from '@/stores/artifactStore'

// 导航项配置。固定角色模型下不再用权限点驱动可见性，而是按 group + 角色推导：
// - content：内容菜单，member/admin 均可见；
// - manage：租户管理菜单（管人/管资产），仅 admin 可见；
// - capability：平台能力配置菜单（模型/Embedding/OCR/检索测试/API Key），属平台底座，
//   全平台一份，仅 Super_Admin 可见可改（capability-config-to-platform）；
// - platform：平台菜单（租户管理），仅 Super_Admin 可见。
// 审计日志归 manage（admin 可见），但 Super_Admin 经下方 SUPER_ADMIN_MENUS 单独放行。
const navItems = [
  // 法条库部署：入口直达全局法条库的内容维护页（仅租户管理员可见）。
  // 取代上游的「知识库」列表入口——本产品线的库范围由下游决定，
  // 管理员只需要维护全局法条库。
  { to: '/legal', label: '法条库', icon: Database, group: 'manage' },
  { to: '/retrieval', label: '检索测试', icon: Search, group: 'capability' },
  { to: '/embed-config', label: 'Embedding', icon: Layers, group: 'capability' },
  { to: '/api-keys', label: 'API Key', icon: Key, group: 'capability' },
  { to: '/tenants', label: '租户管理', icon: Building2, group: 'platform' },
  { to: '/users', label: '用户管理', icon: UsersIcon, group: 'manage' },
  { to: '/audit-logs', label: '审计日志', icon: ScrollText, group: 'manage' },
] as const

// 布局组件：侧边栏 + 主内容区
function Layout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)

  // 路由切换时关闭 Artifact 预览面板：预览内容（法条库文档原件）与具体页面绑定，
  // 离开页面后悬浮的预览已失去上下文，应随之收起。
  const closeArtifact = useArtifactStore((s) => s.closeArtifact)
  useEffect(() => {
    closeArtifact()
  }, [location.pathname, closeArtifact])
  const [profileOpen, setProfileOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const { isSuperAdmin, isAdmin, logout, profile } = useAuth()

  // 菜单可见性（固定角色模型，取代权限点）：
  // - Super_Admin（平台级）：平台菜单（租户管理）+ 平台能力配置（capability）+ 审计日志。
  //   不显示租户级管理（用户）与内容菜单（超管无租户上下文、不参与内容）。
  // - admin（租户管理员）：租户管理菜单（manage，管人/管资产）+ 内容菜单（content）。
  //   不再显示能力配置（capability，已上收平台）。
  // - member（普通成员）：仅内容菜单（content）。
  const SUPER_ADMIN_MENUS = new Set([
    '/tenants',
    '/audit-logs',
    '/embed-config',
    '/retrieval',
    '/api-keys',
  ])
  const visibleNavItems = navItems.filter((item) => {
    if (isSuperAdmin) return SUPER_ADMIN_MENUS.has(item.to)
    if (item.group === 'platform') return false // 平台菜单仅 Super_Admin
    if (item.group === 'capability') return false // 能力配置仅 Super_Admin（已上收平台）
    return isAdmin // manage 菜单仅 admin
  })

  // 内容与菜单一致性守卫：超管为纯平台管理身份，仅允许访问其菜单内的页面
  // （租户管理 / 审计日志）与账号自助页（改密）。系统设置/个人资料已改为账号
  // 菜单弹窗（非路由），不在此列。直接命中法条库等页面时，重定向回
  // "租户管理"，避免出现"左侧无此菜单、右侧却是法条库内容"的错位。
  // 注意：置于所有 hook 调用之后，避免条件式调用 hook。
  // 超管可访问：平台菜单（租户管理/审计日志）、平台能力配置（Embedding/检索测试/
  // API Key，capability-config-to-platform）、账号自助页（改密）。
  const SUPER_ADMIN_ALLOWED_PATHS = new Set([
    '/tenants',
    '/audit-logs',
    '/change-password',
    '/embed-config',
    '/retrieval',
    '/api-keys',
  ])
  if (isSuperAdmin && !SUPER_ADMIN_ALLOWED_PATHS.has(location.pathname)) {
    return <Navigate to="/tenants" replace />
  }

  return (
    <div className="flex h-screen">
      {/* 侧边栏 */}
      <aside
        className={cn(
          'shrink-0 border-r border-sidebar-border bg-sidebar flex flex-col transition-[width] duration-200 ease-in-out overflow-hidden',
          sidebarOpen ? 'w-60' : 'w-12'
        )}
      >
        {/* 展开状态内容 */}
        <div
          className={cn(
            'w-60 h-full flex flex-col transition-opacity duration-200',
            sidebarOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
          )}
        >
          {/* 顶部：标题 + toggle */}
          <div className="flex items-center justify-between px-4 py-3">
            <h1 className="text-lg font-semibold text-sidebar-foreground font-serif">法条库</h1>
            <button
              className="h-7 w-7 flex items-center justify-center rounded-md text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors cursor-pointer"
              onClick={() => setSidebarOpen(false)}
              title="收起侧边栏"
            >
              <PanelLeft className="h-4 w-4" />
            </button>
          </div>

          {/* 常驻按钮区：导航。对话链路已随非召回链路移除（见方案 D7）。 */}
          <div className="px-3 pt-3 pb-2 space-y-1">
            {visibleNavItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-sidebar-primary text-sidebar-primary-foreground'
                      : 'text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
                  )
                }
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </NavLink>
            ))}
          </div>

          {/* 历史对话列表已随对话链路移除（见方案 D7） */}
          <div className="flex-1" />

          {/* 底部：当前登录者（紧凑一栏：头像+用户名+身份）。点击展开账号操作。 */}
          <div className="border-t border-sidebar-border px-3 py-2 relative">
            {/* 展开的操作菜单（个人资料 / 系统设置 / 修改密码 / 退出登录） */}
            {accountMenuOpen && (
              <>
                {/* 点击空白处关闭 */}
                <div className="fixed inset-0 z-10" onClick={() => setAccountMenuOpen(false)} />
                <div className="absolute bottom-full left-3 right-3 mb-1 z-20 rounded-lg border border-sidebar-border bg-sidebar shadow-lg p-1 space-y-0.5">
                  <button
                    onClick={() => { setAccountMenuOpen(false); setProfileOpen(true) }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors cursor-pointer"
                  >
                    <UserCircle className="h-4 w-4" />
                    <span>个人资料</span>
                  </button>
                  <button
                    onClick={() => { setAccountMenuOpen(false); setSettingsOpen(true) }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors cursor-pointer"
                  >
                    <Settings className="h-4 w-4" />
                    <span>系统设置</span>
                  </button>
                  <button
                    onClick={() => { setAccountMenuOpen(false); navigate('/change-password') }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors cursor-pointer"
                  >
                    <KeyRound className="h-4 w-4" />
                    <span>修改密码</span>
                  </button>
                  <button
                    onClick={() => { setAccountMenuOpen(false); logout() }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-sidebar-foreground/80 hover:bg-destructive/10 hover:text-destructive transition-colors cursor-pointer"
                  >
                    <LogOut className="h-4 w-4" />
                    <span>退出登录</span>
                  </button>
                </div>
              </>
            )}
            {/* 紧凑一栏 */}
            <button
              onClick={() => setAccountMenuOpen((v) => !v)}
              className="w-full flex items-center gap-2.5 px-2 py-2 rounded-lg hover:bg-sidebar-accent transition-colors cursor-pointer text-left"
              title="账号"
            >
              {profile?.avatar ? (
                <img src={profile.avatar} alt="" className="h-8 w-8 rounded-full object-cover shrink-0" />
              ) : (
                <div className="h-8 w-8 rounded-full bg-sidebar-primary/15 flex items-center justify-center shrink-0 text-sm font-medium text-sidebar-foreground">
                  {(profile?.username ?? '?').slice(0, 1).toUpperCase()}
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-sidebar-foreground">{profile?.username ?? '—'}</div>
                <div className="truncate text-xs text-sidebar-foreground/60">
                  {profile?.role_label ?? '用户'}
                </div>
              </div>
              <ChevronUp className={cn('h-4 w-4 text-sidebar-foreground/50 shrink-0 transition-transform', accountMenuOpen ? '' : 'rotate-180')} />
            </button>
          </div>
        </div>

        {/* 收起状态内容 */}
        <div
          className={cn(
            'absolute inset-0 w-12 h-full flex flex-col items-center transition-opacity duration-200',
            sidebarOpen ? 'opacity-0 pointer-events-none' : 'opacity-100'
          )}
        >
          {/* 标题缩写 + hover 显示展开图标 */}
          <div
            className="h-[52px] w-full flex items-center justify-center cursor-pointer"
            onClick={() => setSidebarOpen(true)}
            title="展开侧边栏"
          >
            <div className="group h-8 w-8 flex items-center justify-center rounded-md hover:bg-sidebar-accent transition-colors">
              <span className="text-lg font-semibold text-sidebar-foreground font-serif group-hover:hidden">Ar</span>
              <PanelLeft className="h-4 w-4 text-sidebar-foreground hidden group-hover:block" />
            </div>
          </div>

          {/* 导航图标 */}
          <div className="flex flex-col items-center pt-3 px-1">
            {visibleNavItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className="h-10 w-full flex items-center justify-center"
                title={item.label}
              >
                {({ isActive }) => (
                  <div className={cn(
                    'h-8 w-8 flex items-center justify-center rounded-md transition-colors',
                    isActive
                      ? 'bg-sidebar-primary text-sidebar-primary-foreground'
                      : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground'
                  )}>
                    <item.icon className="h-4 w-4" />
                  </div>
                )}
              </NavLink>
            ))}
          </div>
        </div>
      </aside>

      {/* 主内容区 + Artifact 预览面板（flex 行：面板占用空间、从右滑入推挤内容） */}
      <main className="flex-1 min-w-0 flex overflow-hidden">
        <div className="flex-1 min-w-0 overflow-auto bg-background p-6">
          <Outlet />
        </div>
        <ArtifactPanel />
      </main>

      {/* 个人资料弹窗 */}
      <ProfileDialog open={profileOpen} onOpenChange={setProfileOpen} />

      {/* 系统设置弹窗（账号菜单打开）：外观分项所有人可见；切片/检索/平台配置为平台
          能力配置，仅超级管理员可见可改（capability-config-to-platform）。 */}
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        canManageChunk={isSuperAdmin}
        isSuperAdmin={isSuperAdmin}
      />
    </div>
  )
}

export default Layout
