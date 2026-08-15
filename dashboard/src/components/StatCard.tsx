import type { LucideIcon } from 'lucide-react'

interface StatCardProps {
  title: string
  value: string | number
  icon: LucideIcon
  /** 图标颜色，默认为品牌粉 */
  color?: string
  /** 可选的副标题 */
  subtitle?: string
}

export default function StatCard({
  title,
  value,
  icon: Icon,
  color = 'text-cogrid-pink',
  subtitle,
}: StatCardProps) {
  return (
    <div className="bg-cogrid-card rounded-xl p-5 border border-gray-700/50 shadow-lg transition-transform hover:scale-[1.02]">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="text-gray-400 text-sm font-medium">{title}</p>
          <p className="text-2xl font-bold mt-2 text-gray-100 truncate">{value}</p>
          {subtitle && (
            <p className="text-xs text-gray-500 mt-1 truncate">{subtitle}</p>
          )}
        </div>
        <div className="flex-shrink-0 ml-3">
          <Icon className={`w-8 h-8 ${color}`} />
        </div>
      </div>
    </div>
  )
}
