import { AlertCircle, Trophy } from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { apiClient } from '../api'

/* 格式化在线时长 */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} 分钟`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`
  return `${(seconds / 86400).toFixed(1)} 天`
}

/* 前三名奖牌颜色 */
const rankColors = [
  'bg-yellow-500/20 text-yellow-400 border-yellow-500/40', // 金
  'bg-gray-300/20 text-gray-300 border-gray-300/40', // 银
  'bg-orange-700/20 text-orange-600 border-orange-700/40', // 铜
]

export default function Leaderboard() {
  const { data, loading, error } = usePolling(() => apiClient.getLeaderboard())

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

  const entries = data?.leaderboard || []

  return (
    <div className="space-y-6">
      {/* 前三名展示卡片 */}
      {entries.length >= 3 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {entries.slice(0, 3).map((entry, index) => (
            <div
              key={entry.node_id}
              className={`bg-cogrid-card rounded-xl p-6 border-2 ${
                index === 0
                  ? 'border-yellow-500/40'
                  : index === 1
                    ? 'border-gray-300/30'
                    : 'border-orange-700/30'
              } relative overflow-hidden`}
            >
              <div className="flex items-center justify-between mb-3">
                <span
                  className={`inline-flex items-center justify-center w-8 h-8 rounded-full text-sm font-bold border ${
                    rankColors[index]
                  }`}
                >
                  {index + 1}
                </span>
                {index === 0 && (
                  <Trophy className="w-5 h-5 text-yellow-400" />
                )}
              </div>
              <p className="font-mono text-xs text-gray-500 truncate">
                {entry.node_id}
              </p>
              <p className="text-2xl font-bold text-cogrid-pink mt-1">
                {entry.total_credits.toLocaleString()}
              </p>
              <p className="text-xs text-gray-500 mt-1">贡献分</p>
              <div className="mt-3 pt-3 border-t border-gray-700/30 flex justify-between text-xs">
                <span className="text-gray-400">
                  份额 {(entry.share_ratio * 100).toFixed(1)}%
                </span>
                <span className="text-gray-400">
                  在线 {formatDuration(entry.online_seconds)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 排行榜表格 */}
      <div className="bg-cogrid-card rounded-xl border border-gray-700/50 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-700/50 text-gray-400 bg-cogrid-accent/30">
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">排名</th>
                <th className="px-4 py-3 text-left font-medium whitespace-nowrap">节点 ID</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">贡献分</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">份额比例</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">探针成功率</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">探针次数</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">质量因子</th>
                <th className="px-4 py-3 text-right font-medium whitespace-nowrap">在线时长</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-gray-600">
                    暂无排行数据
                  </td>
                </tr>
              ) : (
                entries.map((entry, index) => (
                  <tr
                    key={entry.node_id}
                    className="border-b border-gray-700/30 table-row-hover"
                  >
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold ${
                          index < 3
                            ? rankColors[index]
                            : 'text-gray-500'
                        }`}
                      >
                        {index + 1}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-400">
                      {entry.node_id.length > 16
                        ? entry.node_id.slice(0, 16) + '…'
                        : entry.node_id}
                    </td>
                    <td className="px-4 py-3 text-right font-mono font-semibold text-cogrid-pink">
                      {entry.total_credits.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {(entry.share_ratio * 100).toFixed(2)}%
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {(entry.probe_success_rate * 100).toFixed(1)}%
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-400 text-xs">
                      {entry.probe_success_count} / {entry.probe_total_count}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">
                      {entry.quality_factor.toFixed(3)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300 whitespace-nowrap">
                      {formatDuration(entry.online_seconds)}
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
