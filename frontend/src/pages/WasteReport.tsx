import { useEffect, useState } from 'react'
import { api, ApiError, type WasteReport } from '../api'

function currentMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export default function WasteReportPage() {
  const [month, setMonth] = useState(currentMonth())
  const [report, setReport] = useState<WasteReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!month) return
    setError(null)
    api<WasteReport>(`/reports/waste?month=${encodeURIComponent(month)}`)
      .then(setReport)
      .catch((e) => {
        setReport(null)
        setError(e instanceof ApiError ? e.message : 'Failed to load')
      })
  }, [month])

  return (
    <div>
      <h1>Waste report</h1>
      <label>Month <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      {report && (
        <>
          {report.medicines.length === 0 ? (
            <p>No expired stock wasted in {report.month}.</p>
          ) : (
            <table>
              <thead><tr><th>Medicine</th><th>SKU</th><th>Lots</th><th>Total wasted</th></tr></thead>
              <tbody>
                {report.medicines.map((m) => (
                  <tr key={m.medicine_id}><td>{m.name}</td><td>{m.sku}</td><td>{m.lots_count}</td><td>{m.total_wasted}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <p><strong>Grand total wasted: {report.grand_total}</strong></p>
        </>
      )}
    </div>
  )
}
