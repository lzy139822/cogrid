import axios from 'axios'

const baseURL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1'

const api = axios.create({
  baseURL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

/* ────────── 类型定义 ────────── */

export interface HealthResponse {
  status: string
  service: string
}

export interface PoolStatus {
  online_nodes: number
  total_nodes: number
  available_cpu_cores: number
  available_gpu_count: number
  available_memory_mb: number
  pending_tasks: number
  running_tasks: number
  total_credits: number
  artifacts: unknown[]
}

export interface Node {
  node_id: string
  name: string
  status: string
  intensity: string
  available_cpu: number
  available_gpu: number
  running_tasks: number
  credits: number
  share_ratio: number
  probe_success_rate: number
}

export interface Task {
  task_id: string
  status: string
  task_type: string
  assigned_node: string
  image: string
  created_at?: string
  started_at?: string
  completed_at?: string
}

export interface LeaderboardEntry {
  node_id: string
  total_credits: number
  share_ratio: number
  probe_success_rate: number
  probe_success_count: number
  probe_total_count: number
  quality_factor: number
  online_seconds: number
}

export interface SubmitTaskRequest {
  user_id: string
  image: string
  command: string[]
  cpu_cores: number
  gpu_count: number
  memory_mb: number
  timeout_seconds: number
  preemptible: boolean
}

export interface SubmitTaskResponse {
  task_id: string
  status: string
}

/* ────────── API 客户端 ────────── */

export const apiClient = {
  getHealth: () => api.get<HealthResponse>('/health').then((r) => r.data),

  getPoolStatus: () => api.get<PoolStatus>('/pool/status').then((r) => r.data),

  getNodes: () => api.get<{ nodes: Node[] }>('/nodes').then((r) => r.data),

  getTasks: (limit = 50) =>
    api.get<{ tasks: Task[] }>(`/tasks?limit=${limit}`).then((r) => r.data),

  getTask: (taskId: string) => api.get<Task>(`/tasks/${taskId}`).then((r) => r.data),

  getLeaderboard: (limit = 20) =>
    api.get<{ leaderboard: LeaderboardEntry[] }>(`/leaderboard?limit=${limit}`).then((r) => r.data),

  getContribution: (nodeId: string) =>
    api.get<LeaderboardEntry>(`/contribution/${nodeId}`).then((r) => r.data),

  submitTask: (data: SubmitTaskRequest) =>
    api.post<SubmitTaskResponse>('/tasks/submit', data).then((r) => r.data),
}

export default api
