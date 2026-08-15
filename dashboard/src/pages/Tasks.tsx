import { AlertCircle } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { apiClient } from '../api'

/* 任务状态颜色映射 */
const statusColors: Record<string, string> = {
  pending: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  running: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  completed: 'bg-green-500/20 text-green-400 border-green-500/30',
  failed: 'bg-red-500/20 text-red-400 border-red-500/30',
}

const statusDot: Record<string, string> = {
  pending: 'bg-yellow-400',
  running: 'bg-blue-400 animate-pulse',
  completed: 'bg-green-400',
  failed: 'bg-red-400',
}

/* 任务类型颜色映射 */
const typeColors: Record<string, string> = {
  user_task: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  probe: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  filler: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
}

const typeLabels: Record<string, string> = {
  user_task: '用户任务',
  probe: '探针',
  filler: '填充',
}

export default function Tasks() {
  const { data, loading, error } = usePolling(() => apiClient.getTasks())

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-gray-400 text-lg">加载中...</div>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-red-400">
        <AlertCircle size={20} />
        <span>加载失败: {error}</span>
      </div>
    )
  }

  const tasks = data?.tasks || []

  // 状态统计
  const statusCounts = {
    pending: tasks.filter((t) => t.status === 'pending').length,
    running: tasks.filter((t) => t.status === 'running').length,
    completed: tasks.filter((t) => t.status === 'completed').length,
    failed: tasks.filter((t) => t.status === 'failed').length,
  }

  return (
    <div className="space-y-6">
      {/* 状态统计卡片 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">待处理</p>
          <p className="text-xl font-bold mt-1 text-yellow-400">
            {statusCounts.pending}
          </p>
        </div>
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">运行中</p>
          <p className="text-xl font-bold mt-1 text-blue-400">
            {statusCounts.running}
          </p>
        </div>
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">已完成</p>
          <p className="text-xl font-bold mt-1 text-green-400">
            {statusCounts.completed}
          </p>
        </div>
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">失败</p>
          <p className="text-xl font-bold mt-1 text-red-400">
            {statusCounts.failed}
          </p>
        </div>
      </div>

      {/* 任务表格 */}
      <div className="bg-cogrid-card rounded-xl border border-gray-700/50 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-700/50 text-gray-400 bg-cogrid-accent/30">
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">任务 ID</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">状态</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">类型</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">分配节点</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">镜像</th>
              </tr>
            </thead>
            <tbody>
              {tasks.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-gray-600">
                    暂无任务数据
                  </td>
                </tr>
              ) : (
                tasks.map((task) => (
                  <tr
                    key={task.task_id}
                    className="border-b border-gray-700/30 table-row-hover"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-gray-400">
                      {task.task_id.length > 16
                        ? task.task_id.slice(0, 16) + '…'
                        : task.task_id}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs border ${
                          statusColors[task.status] ||
                          'bg-gray-500/20 text-gray-400 border-gray-500/30'
                        }`}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            statusDot[task.status] || 'bg-gray-400'
                          }`}
                        ></span>
                        {task.status}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs border ${
                          typeColors[task.task_type] ||
                          'bg-gray-500/20 text-gray-400 border-gray-500/30'
                        }`}
                      >
                        {typeLabels[task.task_type] || task.task_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-400">
                      {task.assigned_node || (
                        <span className="text-gray-600">未分配</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-300">
                      {task.image.length > 30
                        ? task.image.slice(0, 30) + '…'
                        : task.image}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
