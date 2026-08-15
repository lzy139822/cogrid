import { useEffect, useRef, useState } from 'react'

/**
 * 轮询 Hook —— 每 interval 毫秒自动调用 fetcher 刷新数据。
 *
 * @param fetcher 返回 Promise<T> 的数据获取函数
 * @param interval 轮询间隔（毫秒），默认 3000
 * @param deps 依赖数组，当依赖变化时重新初始化轮询
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  interval = 3000,
  deps: unknown[] = [],
): { data: T | null; loading: boolean; error: string | null } {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 使用 ref 保存最新的 fetcher，避免频繁重建 interval
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let active = true

    const poll = async () => {
      try {
        const result = await fetcherRef.current()
        if (active) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (active) {
          const message =
            err instanceof Error ? err.message : '数据请求失败'
          setError(message)
        }
      } finally {
        if (active) {
          setLoading(false)
        }
      }
    }

    // 立即执行一次，然后设定定时器
    poll()
    const timer = setInterval(poll, interval)

    return () => {
      active = false
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval, ...deps])

  return { data, loading, error }
}
