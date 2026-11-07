import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'

interface StockInResult { medicine_id: number; lot_id: number; lot_number: string; expiry_date: string; lot_quantity: number }

export default function StockIn() {
  const [name, setName] = useState('')
  const [sku, setSku] = useState('')
  const [lotNumber, setLotNumber] = useState('')
  const [expiry, setExpiry] = useState('')
  const [quantity, setQuantity] = useState('')
  const [threshold, setThreshold] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<StockInResult | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const res = await api<StockInResult>('/stock/in', 'POST', {
        name,
        sku,
        lot_number: lotNumber,
        expiry_date: expiry,
        quantity: Number(quantity),
        reorder_threshold: threshold === '' ? null : Number(threshold),
      })
      setResult(res)
      setLotNumber('')
      setQuantity('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h1>Stock in</h1>
      <label>Medicine name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required /></label>
      <label>SKU<input value={sku} onChange={(e) => setSku(e.target.value)} maxLength={64} required /></label>
      <label>Lot number<input value={lotNumber} onChange={(e) => setLotNumber(e.target.value)} maxLength={64} required /></label>
      <label>Expiry date<input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} required /></label>
      <label>Quantity<input type="number" min={1} max={1000000} step={1} value={quantity} onChange={(e) => setQuantity(e.target.value)} required /></label>
      <label>Reorder threshold (optional)<input type="number" min={0} max={1000000} step={1} value={threshold} onChange={(e) => setThreshold(e.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      {result && <p className="success" role="status">Saved. Lot {result.lot_number} now holds {result.lot_quantity} units.</p>}
      <button type="submit" disabled={busy}>Add stock</button>
    </form>
  )
}
