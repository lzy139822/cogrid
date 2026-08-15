import {
  Cpu,
  CircuitBoard,
  MemoryStick,
  Server,
  ListChecks,
  Clock,
  Coins,
  Package,
  AlertCircle,
} from 'lucide-react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { usePolling } from '../hooks/usePolling'
import { apiClient } from '../api'
import StatCard from '../components/StatCard'

export default function Overview() {
  const { data: pool, loading, error } = usePolling(() => apiClient.getPoolStatus())
  const { data: nodesData } = usePolling(() => apiClient.getNodes())

  if (loading && !pool) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-gray-400 text-lg">加载中...</div>
      </div>
    )
  }

  if (error && !pool) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-red-400">
        <AlertCircle size={20} />
        <span>加载失败: {error}</span>
      </div>
    )
  }

  if (!pool) return null

  // 资源可用量图表数据
  const resourceChart = [
    { name: 'CPU核心', 数量: pool.available_cpu_cores },
    { name: 'GPU', 数量: pool.available_gpu_count },
    { name: '运行任务', 数量: pool.running_tasks },
    { name: '待处理', 数量: pool.pending_tasks },
  ]

  // 节点资源分布图表数据
  const nodeChart = (nodesData?.nodes || []).map((n) => ({
    name: n.name?.length > 8 ? n.name.slice(0, 8) + '…' : n.name || n.node_id.slice(0, 8),
    CPU: n.available_cpu,
    GPU: n.available_gpu,
    任务: n.running_tasks,
  }))

  return (
    <div className="space-y-6">
      {/* 关键指标卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="在线节点"
          value={`${pool.online_nodes} / ${pool.total_nodes}`}
          subtitle="在线 / 总计"
          icon={Server}
          color="text-green-400"
        />
        <StatCard
          title="可用 CPU 核心"
          value={pool.available_cpu_cores}
          icon={Cpu}
          color="text-blue-400"
        />
        <StatCard
          title="可用 GPU"
          value={pool.available_gpu_count}
          icon={CircuitBoard}
          color="text-purple-400"
        />
        <StatCard
          title="可用内存"
          value={pool.available_memory_mb}
          subtitle="MB"
          icon={MemoryStick}
          color="text-yellow-400"
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="运行中任务"
          value={pool.running_tasks}
          icon={ListChecks}
          color="text-yellow-400"
        />
        <StatCard
          title="待处理任务"
          value={pool.pending_tasks}
          icon={Clock}
          color="text-orange-400"
        />
        <StatCard
          title="总贡献分"
          value={pool.total_credits.toLocaleString()}
          icon={Coins}
          color="text-cogrid-pink"
        />
        <StatCard
          title="固存产物数"
          value={pool.artifacts?.length || 0}
          icon={Package}
          color="text-cyan-400"
        />
      </div>

      {/* 图表区域 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 资源可用量 */}
        <div className="bg-cogrid-card rounded-xl p-6 border border-gray-700/50">
          <h2 className="text-lg font-semibold mb-4 text-gray-200">资源概览</h2>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={resourceChart}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a4a" />
              <XAxis dataKey="name" stroke="#888" fontSize={12} />
              <YAxis stroke="#888" fontSize={12} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#16213e',
                  border: '1px solid #333',
                  borderRadius: '8px',
                }}
              />
              <Bar dataKey="数量" fill="#e94560" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* 节点资源分布 */}
        <div className="bg-cogrid-card rounded-xl p-6 border border-gray-700/50">
          <h2 className="text-lg font-semibold mb-4 text-gray-200">节点资源分布</h2>
          {nodeChart.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={nodeChart}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2a4a" />
                <XAxis dataKey="name" stroke="#888" fontSize={11} />
                <YAxis stroke="#888" fontSize={12} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#16213e',
                    border: '1px solid #333',
                    borderRadius: '8px',
                  }}
                />
                <Legend />
                <Bar dataKey="CPU" fill="#0f3460" radius={[4, 4, 0, 0]} />
                <Bar dataKey="GPU" fill="#e94560" radius={[4, 4, 0, 0]} />
                <Bar dataKey="任务" fill="#53a861" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-[280px] text-gray-600">
              暂无节点数据
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
