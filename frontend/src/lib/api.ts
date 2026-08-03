export interface Collection {
  id: string
  name: string
  description: string
  dpi: number | null
  effective_dpi: number
  image_format: string | null
  effective_image_format: string
  jpeg_quality: number | null
  max_edge: number | null
  grayscale: boolean | null
  status: 'pending' | 'ingesting' | 'cached' | 'stale' | 'error'
  status_detail: string
  prefix_hash: string | null
  cached_prefix_token_count: number
  last_measured_hit_rate: number | null
  last_measured_at: string | null
  pages: number
  documents: number
  estimated_tokens: number
  budget_tokens: number
  context_tokens: number
  budget_used_percent: number
  context_used_percent: number
  over_budget: boolean
  created_at: string
  updated_at: string
}

export interface CollectionDetail extends Collection {
  documents_list?: DocumentInfo[]
}

export interface DocumentInfo {
  id: string
  filename: string
  page_count: number
  sequence_start: number
  dpi: number
  created_at: string
  is_last: boolean
}

export interface PageInfo {
  id: string
  document_id: string
  filename: string
  sequence_index: number
  page_number: number
  document_pages: number
  width: number
  height: number
  dpi: number
  byte_size: number
  estimated_tokens: number
  mime_type: string
  image_hash: string
  url: string
}

export interface ChatSummary {
  id: string
  title: string
  selected_collections: string[]
  message_count: number
  created_at: string
  updated_at: string
}

export interface CollectionAnswer {
  collection_id: string
  collection_name: string
  answer_text: string
  reasoning_text: string
  latency_ms: number
  ttft_ms: number
  prompt_tokens: number
  cached_tokens: number
  completion_tokens: number
  cache_hit_rate: number
  error: string
  prefix_matched?: boolean
  streaming?: boolean
}

export interface SynthesisInfo {
  reasoning_text: string
  latency_ms: number
  ttft_ms: number
  prompt_tokens: number
  completion_tokens: number
  skipped: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  collection_answers: CollectionAnswer[]
  synthesis: SynthesisInfo | null
}

export interface SettingSpec {
  key: string
  group: string
  label: string
  type: 'str' | 'int' | 'float' | 'bool' | 'text' | 'select'
  default: unknown
  description: string
  choices: string[]
  minimum: number | null
  maximum: number | null
  launch_param: boolean
  invalidates_cache: boolean
  secret: boolean
  advanced: boolean
}

export interface SettingsPayload {
  values: Record<string, any>
  schema: SettingSpec[]
  launch_command: string
}

export interface MetricsSummary {
  hicache_enabled: boolean
  hicache_host_used_tokens: number | null
  hicache_host_total_tokens: number | null
  hicache_host_utilization: number | null
  cache_hit_rate: number | null
  prompt_tokens_total: number | null
  cached_tokens_total: number | null
  num_running_reqs: number | null
  num_queue_reqs: number | null
  num_used_tokens: number | null
  max_total_num_tokens: number | null
  token_usage: number | null
  gen_throughput: number | null
  ttft_seconds_avg: number | null
  e2e_latency_seconds_avg: number | null
}

export interface TreeNode {
  type: 'root' | 'collection' | 'document' | 'page'
  id: string
  label: string
  tokens: number
  token_offset: number
  cache_level: 'L1' | 'L2' | 'L3' | 'none'
  cache_level_basis: 'gemessen' | 'abgeleitet'
  children?: TreeNode[]
  status?: string
  status_detail?: string
  measured_hit_rate?: number | null
  measured_at?: string | null
  prefix_hash?: string
  cached_prefix_token_count?: number
  pages?: number
  page_number?: number
  sequence_index?: number
  width?: number
  height?: number
  dpi?: number
  byte_size?: number
}

export interface CacheTree {
  root: TreeNode
  totals: {
    collections: number
    documents: number
    pages: number
    tokens: number
    cached_collections: number
  }
  provenance: Record<string, string>
}

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: any,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let detail: any = null
    try {
      detail = await response.json()
    } catch {
      detail = await response.text().catch(() => null)
    }
    const message =
      (typeof detail?.detail === 'string' && detail.detail) ||
      detail?.detail?.message ||
      detail?.message ||
      `HTTP ${response.status}`
    throw new ApiError(message, response.status, detail?.detail ?? detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  // --- collections ---
  listCollections: () => request<Collection[]>('/api/collections'),
  getCollection: (id: string) =>
    request<Collection & { documents: DocumentInfo[] }>(`/api/collections/${id}`),
  createCollection: (body: Partial<Collection>) =>
    request<Collection>('/api/collections', { method: 'POST', body: JSON.stringify(body) }),
  updateCollection: (id: string, body: Record<string, unknown>) =>
    request<Collection & { requires_reconversion: boolean }>(`/api/collections/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteCollection: (id: string) =>
    request<void>(`/api/collections/${id}`, { method: 'DELETE' }),

  uploadDocument: (id: string, file: File, dpi?: number) => {
    const form = new FormData()
    form.append('file', file)
    if (dpi) form.append('dpi', String(dpi))
    return request<{ document_id: string; pages: number; collection: Collection }>(
      `/api/collections/${id}/documents`,
      { method: 'POST', body: form },
    )
  },
  removalImpact: (collectionId: string, documentId: string) =>
    request<{
      is_last: boolean
      invalidates: boolean
      remaining_pages: number
      remaining_tokens_estimate: number
      message: string
    }>(`/api/collections/${collectionId}/documents/${documentId}/removal-impact`),
  deleteDocument: (collectionId: string, documentId: string) =>
    request<{ invalidated: boolean; collection: Collection }>(
      `/api/collections/${collectionId}/documents/${documentId}`,
      { method: 'DELETE' },
    ),
  reorderDocuments: (id: string, documentIds: string[]) =>
    request<{ invalidated: boolean; collection: Collection }>(
      `/api/collections/${id}/reorder`,
      { method: 'POST', body: JSON.stringify({ document_ids: documentIds }) },
    ),

  listPages: (id: string) => request<PageInfo[]>(`/api/collections/${id}/pages`),

  startIngest: (id: string) =>
    request<{ status: string }>(`/api/collections/${id}/ingest`, { method: 'POST' }),
  ingestStatus: (id: string) =>
    request<{ status: string; status_detail: string; job: any }>(
      `/api/collections/${id}/ingest/status`,
    ),

  dpiPreview: (file: File, dpi: number, maxEdge = 0) => {
    const form = new FormData()
    form.append('file', file)
    form.append('dpi', String(dpi))
    form.append('max_edge', String(maxEdge))
    return request<{
      dpi: number
      pages: number
      total_tokens: number
      avg_tokens_per_page: number
      context_percent: number
      pixels_per_token_edge: number
      per_page: { page: number; width: number; height: number; tokens: number }[]
    }>('/api/collections/dpi-preview', { method: 'POST', body: form })
  },

  // --- chats ---
  listChats: (q = '') => request<ChatSummary[]>(`/api/chats?q=${encodeURIComponent(q)}`),
  getChat: (id: string) =>
    request<ChatSummary & { messages: ChatMessage[] }>(`/api/chats/${id}`),
  createChat: (body: { title?: string; selected_collections?: string[] }) =>
    request<ChatSummary>('/api/chats', { method: 'POST', body: JSON.stringify(body) }),
  updateChat: (id: string, body: { title?: string; selected_collections?: string[] }) =>
    request<ChatSummary>(`/api/chats/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteChat: (id: string) => request<void>(`/api/chats/${id}`, { method: 'DELETE' }),

  // --- settings & system ---
  getSettings: () => request<SettingsPayload>('/api/settings'),
  saveSettings: (values: Record<string, unknown>) =>
    request<{
      values: Record<string, any>
      launch_command: string
      invalidated_keys: string[]
      invalidated_collections: number
    }>('/api/settings', { method: 'PUT', body: JSON.stringify({ values }) }),
  resetSettings: (keys: string[]) =>
    request<{ values: Record<string, any>; launch_command: string }>('/api/settings/reset', {
      method: 'POST',
      body: JSON.stringify({ keys }),
    }),

  health: () =>
    request<{ ok: boolean; latency_ms: number; base_url: string; error: string }>(
      '/api/sglang/health',
    ),
  serverInfo: () =>
    request<{ server_info: Record<string, any>; mismatches: any[] }>('/api/sglang/server-info'),
  metrics: () => request<MetricsSummary>('/api/sglang/metrics'),
  flushCache: () =>
    request<{ ok: boolean; message: string; invalidated_collections?: number }>(
      '/api/sglang/flush-cache?timeout=30',
      { method: 'POST' },
    ),
  cacheTree: () => request<CacheTree>('/api/cache/tree'),
  cacheDashboard: () =>
    request<{ tree: CacheTree; metrics: MetricsSummary | null; metrics_available: boolean }>(
      '/api/cache/dashboard',
    ),
}

// --------------------------------------------------------------------- SSE
export type SseHandler = (event: string, data: any) => void

/**
 * POST + SSE.
 *
 * `EventSource` cannot POST, so the stream is read off the fetch body and the
 * `event:`/`data:` framing is parsed here. Partial frames are kept in the
 * buffer until their terminating blank line arrives.
 */
export async function streamQuery(
  body: { chat_id: string | null; question: string; collection_ids: string[] },
  onEvent: SseHandler,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/query/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `HTTP ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      let event = 'message'
      const dataLines: string[] = []
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (!dataLines.length) continue
      try {
        onEvent(event, JSON.parse(dataLines.join('\n')))
      } catch {
        /* a frame we cannot parse is not worth killing the stream over */
      }
    }
  }
}

export function subscribeProgress(collectionId: string, onUpdate: (data: any) => void) {
  const source = new EventSource(`/api/collections/${collectionId}/progress`)
  source.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onUpdate(data)
      if (data.finished) source.close()
    } catch {
      /* ignore malformed frame */
    }
  }
  source.onerror = () => source.close()
  return () => source.close()
}

export { ApiError }
