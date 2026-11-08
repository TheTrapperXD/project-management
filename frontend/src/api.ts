export class ApiError extends Error {
  status: number
  data: unknown
  constructor(status: number, message: string, data: unknown) {
    super(message)
    this.status = status
    this.data = data
  }
}

function messageFrom(data: unknown, fallback: string): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : ''))
        .filter(Boolean)
        .join('; ')
    }
  }
  return fallback
}

const API_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-Requested-With'] = 'pharmacy-app'
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_URL}/api${path}`, {
    method,
    credentials: 'include',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data: unknown = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, messageFrom(data, `Request failed (${res.status})`), data)
  return data as T
}

export interface User { id: number; username: string }
export interface Medicine { id: number; name: string; sku: string; total_quantity: number; reorder_threshold: number }
export interface BucketItem {
  medicine_id: number; name: string; sku: string; lot_number: string
  expiry_date: string; quantity: number; days_left: number
}
export interface Dashboard { red: BucketItem[]; yellow: BucketItem[]; green: BucketItem[]; low_stock: Medicine[] }
export interface Allocation { lot_id: number; lot_number: string; expiry_date: string; taken: number }
export interface WasteReport {
  month: string
  medicines: { medicine_id: number; name: string; sku: string; lots_count: number; total_wasted: number }[]
  lots: { medicine_id: number; name: string; sku: string; lot_number: string; expiry_date: string; quantity: number }[]
  grand_total: number
}
