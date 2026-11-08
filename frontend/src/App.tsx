import { useEffect, useState } from 'react'
import { api, getToken, logout as apiLogout, setUnauthorizedHandler, type User } from './api'
import Login from './pages/Login'
import DashboardPage from './pages/Dashboard'
import StockIn from './pages/StockIn'
import Dispense from './pages/Dispense'
import WasteReportPage from './pages/WasteReport'

type Page = 'dashboard' | 'stock-in' | 'dispense' | 'waste'

const PAGES: { id: Page; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'stock-in', label: 'Stock In' },
  { id: 'dispense', label: 'Dispense' },
  { id: 'waste', label: 'Waste Report' },
]

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(true)
  const [page, setPage] = useState<Page>('dashboard')

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    if (!getToken()) {
      setChecking(false)
      return
    }
    api<User>('/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setChecking(false))
  }, [])

  async function logout() {
    await apiLogout()
    setUser(null)
  }

  if (checking) return <p className="center">Loading…</p>
  if (!user) return <Login onLogin={setUser} />

  return (
    <div className="app">
      <header className="nav">
        <strong>ExpiryGuard</strong>
        <nav>
          {PAGES.map((p) => (
            <button key={p.id} className={page === p.id ? 'active' : ''} onClick={() => setPage(p.id)}>
              {p.label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <span>{user.username}</span>
        <button onClick={logout}>Log out</button>
      </header>
      <main>
        {page === 'dashboard' && <DashboardPage />}
        {page === 'stock-in' && <StockIn />}
        {page === 'dispense' && <Dispense />}
        {page === 'waste' && <WasteReportPage />}
      </main>
    </div>
  )
}
