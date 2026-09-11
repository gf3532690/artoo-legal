import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import Layout from './components/Layout'
import KnowledgeBase from './pages/KnowledgeBase'
import Documents from './pages/Documents'
import KnowledgeGraph from './pages/KnowledgeGraph'
import LegalLibrary from './pages/LegalLibrary'
import Retrieval from './pages/Retrieval'
import ApiKeys from './pages/ApiKeys'
import Models from './pages/Models'
import EmbedConfig from './pages/EmbedConfig'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Register from './pages/Register'
import ChangePassword from './pages/ChangePassword'
import Tenants from './pages/Tenants'
import Users from './pages/Users'
import AuditLogs from './pages/AuditLogs'
import InviteAccept from './pages/InviteAccept'
import { useAuth } from './lib/auth-context'

// 路由守卫：未登录跳登录页；强制改密时跳改密页（仅放行改密页本身）。
function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, mustChangePassword, ready } = useAuth()
  const location = useLocation()

  // 初始权限/登录态加载中：避免闪烁，渲染空白
  if (!ready) return null

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  if (mustChangePassword && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }
  return <>{children}</>
}

// 登录后默认落地页：超管是纯平台装配身份 → 租户管理；
// 租户管理员是需要验收语料的人 → 检索测试（能直接查「我传的法条检索得到吗」）。
// 不落「全局法条库」——那是维护动作，登录后第一件事通常不是改语料。
function DefaultLanding() {
  const { isSuperAdmin } = useAuth()
  return <Navigate to={isSuperAdmin ? '/tenants' : '/retrieval'} replace />
}

// 根路径入口：未登录展示炫酷落地页；已登录则按身份跳转到对应首页。
function RootEntry() {
  const { isAuthenticated, ready } = useAuth()
  if (!ready) return null
  if (!isAuthenticated) return <Landing />
  return <DefaultLanding />
}

// 应用根组件：路由配置
function App() {
  return (
    <Routes>
      <Route path="/" element={<RootEntry />} />
      <Route path="/landing" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/change-password" element={<ChangePassword />} />
      <Route path="/invite/:token" element={<InviteAccept />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        {/* 法条库部署：入口直达全局法条库的内容维护页（不新增检索端点）。
            必须挂在 Layout 下：放在顶层时这个页面没有左侧菜单，用户进去就出不来了。 */}
        <Route path="legal" element={<LegalLibrary />} />
        <Route path="knowledge-bases" element={<KnowledgeBase />} />
        <Route path="knowledge-bases/:id" element={<Documents />} />
        <Route path="knowledge-bases/:id/graph" element={<KnowledgeGraph />} />
        <Route path="retrieval" element={<Retrieval />} />
        <Route path="models" element={<Models />} />
        <Route path="embed-config" element={<EmbedConfig />} />
        <Route path="api-keys" element={<ApiKeys />} />
        <Route path="tenants" element={<Tenants />} />
        <Route path="users" element={<Users />} />
        <Route path="audit-logs" element={<AuditLogs />} />
      </Route>
    </Routes>
  )
}

export default App
