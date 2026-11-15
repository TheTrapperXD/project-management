import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, formatBaht, type BucketItem, type Dashboard, type WriteoffReason, type WriteoffResult } from '../api'

const REASONS: { value: WriteoffReason; label: string }[] = [
  { value: 'expiring', label: 'Expiring soon' },
  { value: 'damaged', label: 'Damaged' },
  { value: 'recalled', label: 'Recalled' },
  { value: 'other', label: 'Other' },
]

function PullControls({ item, onDone, onCancel }: {
  item: BucketItem
  onDone: (r: WriteoffResult) => void
  onCancel: () => void
}) {
  const [quantity, setQuantity] = useState(String(item.quantity))
  const [reason, setReason] = useState<WriteoffReason>('expiring')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function confirm(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDone(await api<WriteoffResult>('/stock/writeoff', 'POST', { lot_id: item.lot_id, quantity: Number(quantity), reason }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Request failed')
      setBusy(false)
    }
  }

  return (
    <form className="pull" onSubmit={confirm}>
      <label>Qty
        <input type="number" min={1} max={item.quantity} step={1} value={quantity} onChange={(e) => setQuantity(e.target.value)} required />
      </label>
      <label>Reason
        <select value={reason} onChange={(e) => setReason(e.target.value as WriteoffReason)}>
          {REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
      </label>
      <button type="submit" disabled={busy}>Confirm</button>
      <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  )
}

interface PullProps {
  pulling: number | null
  setPulling: (lotId: number | null) => void
  onPulled: (r: WriteoffResult) => void
}

function LotRow({ item, pulling, setPulling, onPulled }: { item: BucketItem } & PullProps) {
  return (
    <tr>
      <td>{item.name} ({item.sku})</td>
      <td>{item.lot_number}</td>
      <td>{item.expiry_date}</td>
      <td>{item.quantity}</td>
      <td>{item.days_left}</td>
      <td>
        {pulling === item.lot_id ? (
          <PullControls item={item} onDone={onPulled} onCancel={() => setPulling(null)} />
        ) : (
          <button type="button" onClick={() => setPulling(item.lot_id)}>Pull</button>
        )}
      </td>
    </tr>
  )
}

function LotTable({ items, ...pull }: { items: BucketItem[] } & PullProps) {
  if (items.length === 0) return <p>None.</p>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Medicine</th><th>Lot</th><th>Expiry</th><th>Qty</th><th>Days left</th><th></th></tr>
        </thead>
        <tbody>
          {items.map((i) => <LotRow key={i.lot_id} item={i} {...pull} />)}
        </tbody>
      </table>
    </div>
  )
}

function Section({ cls, title, items, ...pull }: { cls: string; title: string; items: BucketItem[] } & PullProps) {
  return (
    <section className={`bucket ${cls}`}>
      <h2>{title} ({items.length})</h2>
      <LotTable items={items} {...pull} />
    </section>
  )
}

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pulling, setPulling] = useState<number | null>(null)
  const [pulled, setPulled] = useState<WriteoffResult | null>(null)

  const load = useCallback(() => {
    api<Dashboard>('/dashboard')
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Failed to load'))
  }, [])
  useEffect(load, [load])

  function onPulled(r: WriteoffResult) {
    setPulling(null)
    setPulled(r)
    load()
  }

  if (error && !data) return <p className="error" role="alert">{error}</p>
  if (!data) return <p>Loading…</p>

  const sectionProps = { pulling, setPulling, onPulled }
  return (
    <div>
      <h1>Expiry dashboard</h1>
      {error && <p className="error" role="alert">{error}</p>}
      {pulled && (
        <p className="success" role="status">
          Pulled {pulled.written_off} from {pulled.medicine_name} lot {pulled.lot_number} ({pulled.remaining} remaining). Value: {formatBaht(pulled.value)}
        </p>
      )}
      <Section cls="red" title="RED — Expires within 30 days" items={data.red} {...sectionProps} />
      <Section cls="yellow" title="YELLOW — Expires in 31-60 days" items={data.yellow} {...sectionProps} />
      <Section cls="green" title="GREEN — Expires in 61-90 days" items={data.green} {...sectionProps} />
      <details className="bucket">
        <summary>Later: expires in over 90 days ({data.later.length})</summary>
        <LotTable items={data.later} {...sectionProps} />
      </details>
      <section className="bucket">
        <h2>Low stock ({data.low_stock.length})</h2>
        {data.low_stock.length === 0 ? (
          <p>None.</p>
        ) : (
          <ul>
            {data.low_stock.map((m) => (
              <li key={m.id}>{m.name} ({m.sku}): {m.total_quantity} in stock, reorder at {m.reorder_threshold}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
