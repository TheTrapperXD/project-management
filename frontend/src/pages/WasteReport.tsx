import { useEffect, useState } from 'react'
import { api, ApiError, formatBaht, type WasteReport } from '../api'

const REASON_LABELS: Record<string, string> = {
  expired: 'Expired',
  expiring: 'Expiring soon',
  damaged: 'Damaged',
  recalled: 'Recalled',
  other: 'Other',
}

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
            <p>No stock wasted in {report.month}.</p>
          ) : (
            <>
              <div className="table-wrap">
              <table>
                <thead><tr><th>Medicine</th><th>SKU</th><th>Lots</th><th>Units wasted</th><th>Value</th><th>Unvalued units</th></tr></thead>
                <tbody>
                  {report.medicines.map((m) => (
                    <tr key={m.medicine_id}>
                      <td>{m.name}</td><td>{m.sku}</td><td>{m.lots_count}</td><td>{m.total_wasted}</td>
                      <td>{formatBaht(m.wasted_value)}</td><td>{m.unvalued_units}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              <h2>By reason</h2>
              <div className="table-wrap">
              <table>
                <thead><tr><th>Reason</th><th>Units</th><th>Value</th><th>Unvalued units</th></tr></thead>
                <tbody>
                  {report.by_reason.map((r) => (
                    <tr key={r.reason ?? ''}>
                      <td>{r.reason === null ? '—' : REASON_LABELS[r.reason] ?? r.reason}</td>
                      <td>{r.units}</td><td>{formatBaht(r.value)}</td><td>{r.unvalued_units}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </>
          )}
          <p>
            <strong>Grand total: {report.grand_total} units, {formatBaht(report.grand_total_value)}</strong>
            {report.grand_total_unvalued_units > 0 && <> ({report.grand_total_unvalued_units} units have no unit cost and are not included in the value)</>}
          </p>
        </>
      )}
    </div>
  )
}
