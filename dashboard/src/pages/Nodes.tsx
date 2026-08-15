import { AlertCircle } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { apiClient } from '../api'

/* 状态颜色映射 */
const statusColors: Record<string, string> = {
  online: 'bg-green-500/20 text-green-400 border-green-500/30',
  offline: 'bg-red-500/20 text-red-400 border-red-500/30',
  pending: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  running: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
}

const statusDot: Record<string, string> = {
  online: 'bg-green-400',
  offline: 'bg-red-400',
  pending: 'bg-yellow-400',
  running: 'bg-blue-400',
}

/* 强度档位颜色 */
const intensityColors: Record<string, string> = {
  low: 'text-green-400',
  medium: 'text-yellow-400',
  high: 'text-orange-400',
  critical: 'text-red-400',
}

export default function Nodes() {
  const { data, loading, error } = usePolling(() => apiClient.getNodes())

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

  const nodes = data?.nodes || []

  return (
    <div className="space-y-6">
      {/* 节点统计概要 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">总节点数</p>
          <p className="text-xl font-bold mt-1">{nodes.length}</p>
        </div>
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">在线</p>
          <p className="text-xl font-bold mt-1 text-green-400">
            {nodes.filter((n) => n.status === 'online').length}
          </p>
        </div>
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">离线</p>
          <p className="text-xl font-bold mt-1 text-red-400">
            {nodes.filter((n) => n.status === 'offline').length}
          </p>
        </div>
        <div className="bg-cogrid-card rounded-lg p-4 border border-gray-700/50">
          <p className="text-xs text-gray-500">总 CPU 核心</p>
          <p className="text-xl font-bold mt-1">
            {nodes.reduce((sum, n) => sum + n.available_cpu, 0)}
          </p>
        </div>
      </div>

      {/* 节点表格 */}
      <div className="bg-cogrid-card rounded-xl border border-gray-700/50 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-700/50 text-gray-400 bg-cogrid-accent/30">
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">节点 ID</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">名称</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">状态</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">强度</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">CPU</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">GPU</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">运行任务</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">贡献分</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">份额</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">探针成功率</th>
              </tr>
            </thead>
            <tbody>
              {nodes.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-4 py-12 text-center text-gray-600">
                    暂无节点数据
                  </td>
                </tr>
              ) : (
                nodes.map((node) => (
                  <tr
                    key={node.node_id}
                    className="border-b border-gray-700/30 table-row-hover"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-gray-400">
                      {node.node_id.length > 12
                        ? node.node_id.slice(0, 12) + '…'
                        : node.node_id}
                    </td>
                    <td className="px-4 py-3 text-gray-200">{node.name || '-'}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs border ${
                          statusColors[node.status] ||
                          'bg-gray-500/20 text-gray-400 border-gray-500/30'
                        }`}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            statusDot[node.status] || 'bg-gray-400'
                          }`}
                        ></span>
                        {node.status}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={
                          intensityColors[node.intensity] || 'text-gray-400'
                        }
                      >
                        {node.intensity || '-'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {node.available_cpu}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {node.available_gpu}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {node.running_tasks}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-cogrid-pink">
                      {node.credits.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {(node.share_ratio * 100).toFixed(1)}%
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {(node.probe_success_rate * 100).toFixed(1)}%
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
