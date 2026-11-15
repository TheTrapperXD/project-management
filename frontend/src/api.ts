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
        .map((d) => {
          if (!d || typeof d !== 'object' || !('msg' in d)) return ''
          const msg = String((d as { msg: unknown }).msg)
          // Prefix with the field name: the last element of the error location.
          const loc = (d as { loc?: unknown }).loc
          const field = Array.isArray(loc) && loc.length > 0 ? String(loc[loc.length - 1]) : ''
          return field ? `${field}: ${msg}` : msg
        })
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

const TIMEOUT_MS = 90_000
const SLOW_MS = 4_000
const GET_RETRIES = 12
const RETRY_DELAY_MS = 5_000
export const UNREACHABLE_MESSAGE = 'Cannot reach the server. If it was idle, wait a minute and try again.'

// Number of requests that are slow (pending > SLOW_MS) or retrying; drives the wake-up banner.
let wakingCount = 0
let onWaking: (waking: boolean) => void = () => {}
export function setWakingHandler(fn: (waking: boolean) => void): void {
  onWaking = fn
  fn(wakingCount > 0)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function fetchWithTimeout(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (method !== 'GET') headers['X-Requested-With'] = 'pharmacy-app'
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const init: RequestInit = {
    method,
    credentials: 'omit',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  }

  let slow = false
  const markSlow = () => {
    if (slow) return
    slow = true
    wakingCount += 1
    onWaking(true)
  }
  const slowTimer = setTimeout(markSlow, SLOW_MS)

  let res: Response
  try {
    // Only GET is retried: other methods are not idempotent.
    const retries = method === 'GET' ? GET_RETRIES : 0
    for (let attempt = 0; ; attempt++) {
      try {
        res = await fetchWithTimeout(`${API_URL}/api${path}`, init)
        break
      } catch (err) {
        // A sleeping server answers without CORS headers, which fetch reports as a TypeError.
        if (!(err instanceof TypeError) || attempt >= retries) throw new ApiError(0, UNREACHABLE_MESSAGE, null)
        markSlow()
        await sleep(RETRY_DELAY_MS)
      }
    }
  } finally {
    clearTimeout(slowTimer)
    if (slow) {
      wakingCount -= 1
      onWaking(wakingCount > 0)
    }
  }

  const data: unknown = res.status === 204 ? null : await res.json().catch(() => null)
  if (res.status === 401 && path !== '/auth/login') {
    clearToken()
    onUnauthorized()
  }
  if (!res.ok) throw new ApiError(res.status, messageFrom(data, `Request failed (${res.status})`), data)
  return data as T
}

/** Fire-and-forget: wakes a sleeping server as early as possible. The result is ignored. */
export function wakeServer(): void {
  api('/health').catch(() => {})
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
  expiry_date: string; quantity: number; days_left: number; lot_id: number
}
export interface Dashboard {
  red: BucketItem[]; yellow: BucketItem[]; green: BucketItem[]; later: BucketItem[]; low_stock: Medicine[]
}
export interface Allocation { lot_id: number; lot_number: string; expiry_date: string; taken: number }
/** Money arrives from the API as a 2-decimal string (e.g. "12.50") or null when unknown. Display only. */
export type Money = string | null

export function formatBaht(value: Money): string {
  return value === null ? '—' : `฿${value}`
}

export type WriteoffReason = 'expiring' | 'damaged' | 'recalled' | 'other'

export interface WriteoffResult {
  lot_id: number; medicine_name: string; lot_number: string
  written_off: number; remaining: number; value: Money
}

export interface WasteReport {
  month: string
  medicines: {
    medicine_id: number; name: string; sku: string; lots_count: number
    total_wasted: number; wasted_value: Money; unvalued_units: number
  }[]
  by_reason: { reason: string | null; units: number; value: Money; unvalued_units: number }[]
  lots: {
    medicine_id: number; name: string; sku: string; lot_number: string; expiry_date: string
    quantity: number; reason: string | null; unit_cost: Money; value: Money
  }[]
  grand_total: number
  grand_total_value: Money
  grand_total_unvalued_units: number
}
