import { useEffect, useState } from 'react'
import { api, ApiError, type BucketItem, type Dashboard } from '../api'

function Section({ cls, title, items }: { cls: string; title: string; items: BucketItem[] }) {
  return (
    <section className={`bucket ${cls}`}>
      <h2>{title} ({items.length})</h2>
      {items.length === 0 ? (
        <p>None.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Medicine</th><th>Lot</th><th>Expiry</th><th>Qty</th><th>Days left</th></tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={`${i.medicine_id}-${i.lot_number}`}>
                <td>{i.name} ({i.sku})</td>
                <td>{i.lot_number}</td>
                <td>{i.expiry_date}</td>
                <td>{i.quantity}</td>
                <td>{i.days_left}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<Dashboard>('/dashboard')
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Failed to load'))
  }, [])

  if (error) return <p className="error" role="alert">{error}</p>
  if (!data) return <p>Loading…</p>

  return (
    <div>
      <h1>Expiry dashboard</h1>
      <Section cls="red" title="RED — Expires within 30 days" items={data.red} />
      <Section cls="yellow" title="YELLOW — Expires in 31-60 days" items={data.yellow} />
      <Section cls="green" title="GREEN — Expires in 61-90 days" items={data.green} />
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
