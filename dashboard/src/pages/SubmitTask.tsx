import { useState } from 'react'
import { Send, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { apiClient, type SubmitTaskResponse } from '../api'

interface FormState {
  user_id: string
  image: string
  command: string
  cpu_cores: number
  gpu_count: number
  memory_mb: number
  timeout_seconds: number
  preemptible: boolean
}

const initialState: FormState = {
  user_id: '',
  image: '',
  command: '',
  cpu_cores: 1,
  gpu_count: 0,
  memory_mb: 512,
  timeout_seconds: 300,
  preemptible: true,
}

export default function SubmitTask() {
  const [form, setForm] = useState<FormState>(initialState)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<SubmitTaskResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement>,
    field: keyof FormState,
  ) => {
    const value =
      e.target.type === 'checkbox'
        ? e.target.checked
        : e.target.type === 'number'
          ? Number(e.target.value)
          : e.target.value
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    setResult(null)

    try {
      const command = form.command.split(/\s+/).filter(Boolean)
      const res = await apiClient.submitTask({
        user_id: form.user_id,
        image: form.image,
        command,
        cpu_cores: Number(form.cpu_cores),
        gpu_count: Number(form.gpu_count),
        memory_mb: Number(form.memory_mb),
        timeout_seconds: Number(form.timeout_seconds),
        preemptible: form.preemptible,
      })
      setResult(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="bg-cogrid-card rounded-xl border border-gray-700/50 overflow-hidden">
        {/* 表单头部 */}
        <div className="px-6 py-4 border-b border-gray-700/50 bg-cogrid-accent/20">
          <h2 className="text-lg font-semibold text-white">提交新任务</h2>
          <p className="text-sm text-gray-400 mt-1">
            填写以下参数，提交后将分配到可用节点执行
          </p>
        </div>

        {/* 表单内容 */}
        <form onSubmit={handleSubmit} className="p-6 space-y-5">
          {/* 用户 ID */}
          <div>
            <label className="block text-sm text-gray-400 mb-1.5">
              用户 ID <span className="text-cogrid-pink">*</span>
            </label>
            <input
              type="text"
              value={form.user_id}
              onChange={(e) => handleChange(e, 'user_id')}
              placeholder="例如: user-001"
              className="input-field"
              required
            />
          </div>

          {/* 镜像 */}
          <div>
            <label className="block text-sm text-gray-400 mb-1.5">
              容器镜像 <span className="text-cogrid-pink">*</span>
            </label>
            <input
              type="text"
              value={form.image}
              onChange={(e) => handleChange(e, 'image')}
              placeholder="例如: ubuntu:22.04"
              className="input-field"
              required
            />
          </div>

          {/* 命令 */}
          <div>
            <label className="block text-sm text-gray-400 mb-1.5">
              执行命令（空格分隔）
            </label>
            <input
              type="text"
              value={form.command}
              onChange={(e) => handleChange(e, 'command')}
              placeholder="例如: echo hello world"
              className="input-field"
            />
            <p className="text-xs text-gray-600 mt-1">
              多个参数用空格分隔，将自动拆分为数组
            </p>
          </div>

          {/* CPU / GPU */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">CPU 核数</label>
              <input
                type="number"
                value={form.cpu_cores}
                onChange={(e) => handleChange(e, 'cpu_cores')}
                min={1}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">GPU 数量</label>
              <input
                type="number"
                value={form.gpu_count}
                onChange={(e) => handleChange(e, 'gpu_count')}
                min={0}
                className="input-field"
              />
            </div>
          </div>

          {/* 内存 / 超时 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">
                内存 (MB)
              </label>
              <input
                type="number"
                value={form.memory_mb}
                onChange={(e) => handleChange(e, 'memory_mb')}
                min={128}
                step={128}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1.5">
                超时时间 (秒)
              </label>
              <input
                type="number"
                value={form.timeout_seconds}
                onChange={(e) => handleChange(e, 'timeout_seconds')}
                min={1}
                className="input-field"
              />
            </div>
          </div>

          {/* 可抢占 */}
          <div className="flex items-center gap-3 pt-1">
            <input
              type="checkbox"
              id="preemptible"
              checked={form.preemptible}
              onChange={(e) => handleChange(e, 'preemptible')}
              className="w-4 h-4 accent-cogrid-pink cursor-pointer"
            />
            <label
              htmlFor="preemptible"
              className="text-sm text-gray-300 cursor-pointer select-none"
            >
              允许抢占（高优先级任务可中断此任务）
            </label>
          </div>

          {/* 提交按钮 */}
          <div className="pt-2">
            <button type="submit" disabled={submitting} className="btn-primary">
              {submitting ? (
                <span className="flex items-center justify-center gap-2">
                  <Loader2 className="w-5 h-5 animate-spin" />
                  提交中...
                </span>
              ) : (
                <span className="flex items-center justify-center gap-2">
                  <Send className="w-5 h-5" />
                  提交任务
                </span>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* 成功结果 */}
      {result && (
        <div className="bg-green-500/10 border border-green-500/30 rounded-xl p-6">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-6 h-6 text-green-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-green-400 font-semibold">任务提交成功</p>
              <div className="mt-3 space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-400">任务 ID：</span>
                  <span className="font-mono text-sm text-gray-200 bg-cogrid-bg px-2 py-0.5 rounded">
                    {result.task_id}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-400">状态：</span>
                  <span className="text-sm text-green-400">{result.status}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-6">
          <div className="flex items-start gap-3">
            <XCircle className="w-6 h-6 text-red-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-red-400 font-semibold">提交失败</p>
              <p className="text-sm text-gray-400 mt-1">{error}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
