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

const TOKEN_KEY = 'eg_token'

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function setToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* storage unavailable: session will not persist across reloads */
  }
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (method !== 'GET') headers['X-Requested-With'] = 'pharmacy-app'
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_URL}/api${path}`, {
    method,
    credentials: 'omit',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data: unknown = res.status === 204 ? null : await res.json().catch(() => null)
  if (res.status === 401 && path !== '/auth/login') {
    clearToken()
    onUnauthorized()
  }
  if (!res.ok) throw new ApiError(res.status, messageFrom(data, `Request failed (${res.status})`), data)
  return data as T
}

export interface User { id: number; username: string }

export async function login(username: string, password: string): Promise<User> {
  const t = await api<{ access_token: string }>('/auth/login', 'POST', { username, password })
  setToken(t.access_token)
  try {
    return await api<User>('/auth/me')
  } catch (err) {
    clearToken()
    throw err
  }
}

export async function logout(): Promise<void> {
  try {
    await api('/auth/logout', 'POST')
  } catch {
    /* best-effort: the token is discarded either way */
  }
  clearToken()
}
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
