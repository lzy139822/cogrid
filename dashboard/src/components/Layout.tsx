import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Server,
  Trophy,
  ListChecks,
  Send,
  Menu,
  X,
  Activity,
} from 'lucide-react'

const navItems = [
  { to: '/', label: '概览', icon: LayoutDashboard, end: true },
  { to: '/nodes', label: '节点', icon: Server, end: false },
  { to: '/leaderboard', label: '排行榜', icon: Trophy, end: false },
  { to: '/tasks', label: '任务', icon: ListChecks, end: false },
  { to: '/submit', label: '提交任务', icon: Send, end: false },
]

const pageTitleMap: Record<string, string> = {
  '/': '算力池概览',
  '/nodes': '节点列表',
  '/leaderboard': '贡献排行榜',
  '/tasks': '任务列表',
  '/submit': '提交任务',
}

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()
  const pageTitle = pageTitleMap[location.pathname] || 'Cogrid'

  return (
    <div className="min-h-screen bg-cogrid-bg">
      {/* ───── 移动端顶部栏 ───── */}
      <header className="lg:hidden fixed top-0 left-0 right-0 z-50 flex items-center justify-between px-4 py-3 bg-cogrid-card border-b border-gray-700/50">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-cogrid-pink" />
          <span className="text-lg font-bold text-cogrid-pink">Cogrid</span>
        </div>
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="p-2 text-gray-300 hover:text-white transition-colors"
          aria-label="切换菜单"
        >
          {sidebarOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </header>

      {/* ───── 侧边栏 ───── */}
      <aside
        className={`fixed top-0 left-0 z-40 h-screen w-64 bg-cogrid-card border-r border-gray-700/50 transition-transform duration-300 lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Logo 区域 */}
        <div className="p-6 border-b border-gray-700/50">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-cogrid-pink/20 flex items-center justify-center">
              <Activity className="w-6 h-6 text-cogrid-pink" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Cogrid</h1>
              <p className="text-xs text-gray-500">算力合作社</p>
            </div>
          </div>
        </div>

        {/* 导航菜单 */}
        <nav className="px-4 py-4">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-4 py-3 rounded-lg mb-1 transition-colors ${
                  isActive
                    ? 'bg-cogrid-accent text-white shadow-md'
                    : 'text-gray-400 hover:bg-cogrid-accent/40 hover:text-white'
                }`
              }
            >
              <item.icon size={20} />
              <span className="text-sm font-medium">{item.label}</span>
            </NavLink>
          ))}
        </nav>

        {/* 底部信息 */}
        <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-gray-700/50">
          <p className="text-xs text-gray-600 text-center">
            Cogrid Dashboard v0.1
          </p>
        </div>
      </aside>

      {/* ───── 移动端遮罩层 ───── */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ───── 主内容区域 ───── */}
      <div className="lg:ml-64">
        {/* 桌面端标题栏 */}
        <header className="hidden lg:flex items-center justify-between px-8 py-5 bg-cogrid-card/50 border-b border-gray-700/50 sticky top-0 z-20 backdrop-blur-sm">
          <h2 className="text-xl font-semibold text-white">{pageTitle}</h2>
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
            <span>实时数据 · 每 3 秒刷新</span>
          </div>
        </header>

        <main className="p-4 lg:p-8 mt-14 lg:mt-0">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
