import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type Allocation, type Medicine } from '../api'

export default function Dispense() {
  const [medicines, setMedicines] = useState<Medicine[]>([])
  const [medicineId, setMedicineId] = useState('')
  const [quantity, setQuantity] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [allocations, setAllocations] = useState<Allocation[] | null>(null)
  const [busy, setBusy] = useState(false)

  function load() {
    api<Medicine[]>('/medicines')
      .then(setMedicines)
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Failed to load medicines'))
  }
  useEffect(load, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setAllocations(null)
    try {
      const res = await api<{ allocations: Allocation[] }>('/stock/out', 'POST', {
        medicine_id: Number(medicineId),
        quantity: Number(quantity),
      })
      setAllocations(res.allocations)
      setQuantity('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h1>Dispense</h1>
      <label>Medicine
        <select value={medicineId} onChange={(e) => setMedicineId(e.target.value)} required>
          <option value="">Select…</option>
          {medicines.map((m) => (
            <option key={m.id} value={m.id}>{m.name} ({m.sku}) — {m.total_quantity} available</option>
          ))}
        </select>
      </label>
      <label>Quantity<input type="number" min={1} max={1000000} step={1} value={quantity} onChange={(e) => setQuantity(e.target.value)} required /></label>
      {error && <p className="error" role="alert">{error}</p>}
      {allocations && (
        <div className="success" role="status">
          <p>Dispensed. Deducted from these lots (earliest expiry first):</p>
          <ul>
            {allocations.map((a) => (
              <li key={a.lot_id}>Lot {a.lot_number} (expires {a.expiry_date}): {a.taken}</li>
            ))}
          </ul>
        </div>
      )}
      <button type="submit" disabled={busy}>Dispense</button>
    </form>
  )
}
