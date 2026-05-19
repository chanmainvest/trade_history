import { useState, useEffect, useRef } from 'react'
import type { GlobalSettings } from '../App'
import type { Strings } from '../i18n'

interface Position {
  symbol: string
  asset_type: string
  quantity: number
  avg_cost: number
  currency: string
  market_price: number | null
  market_value: number | null
}

interface AccountGroup {
  group_key: string
  institution: string
  stocks: Position[]
  options: Position[]
  total_market_value: number
  as_of_date?: string
}

interface Props {
  settings: GlobalSettings
  s: Strings
}

type ViewMode = 'current' | 'monthly'

function fmtMv(n: number | null): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString('en-CA', { style: 'currency', currency: 'CAD' })
}

function PositionTable({ positions, s }: { positions: Position[]; s: Strings }) {
  if (positions.length === 0) return null
  return (
    <div className="table-wrap" style={{ marginBottom: 8 }}>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th style={{ textAlign: 'right' }}>Qty</th>
            <th style={{ textAlign: 'right' }}>Avg Cost</th>
            <th style={{ textAlign: 'right' }}>Price</th>
            <th style={{ textAlign: 'right' }}>Mkt Value</th>
            <th>CCY</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => {
            const gain = p.market_value !== null
              ? p.market_value - p.avg_cost * p.quantity
              : null
            return (
              <tr key={i}>
                <td><strong>{p.symbol}</strong></td>
                <td style={{ textAlign: 'right' }}>{p.quantity.toLocaleString()}</td>
                <td style={{ textAlign: 'right' }}>{fmtMv(p.avg_cost)}</td>
                <td style={{ textAlign: 'right' }}>{fmtMv(p.market_price)}</td>
                <td
                  style={{ textAlign: 'right' }}
                  className={gain !== null ? (gain >= 0 ? 'positive' : 'negative') : ''}
                >
                  {fmtMv(p.market_value)}
                </td>
                <td>{p.currency}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function AssetValueTab({ settings, s }: Props) {
  const [viewMode, setViewMode] = useState<ViewMode>('current')
  const [months, setMonths] = useState<string[]>([])
  const [selectedMonth, setSelectedMonth] = useState<string>('')
  const [groups, setGroups] = useState<AccountGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // Fetch available months when switching to monthly view
  useEffect(() => {
    if (viewMode !== 'monthly') return
    fetch('/monthly-balances/months')
      .then(r => r.json())
      .then((data: string[]) => {
        setMonths(data)
        if (data.length > 0 && !selectedMonth) setSelectedMonth(data[0])
      })
      .catch(() => setMonths([]))
  }, [viewMode])

  // Fetch data based on view mode
  useEffect(() => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(false)

    let url: string
    if (viewMode === 'monthly' && selectedMonth) {
      const params = new URLSearchParams({
        year_month: selectedMonth,
        group_by: 'account',
        currency: settings.currency,
      })
      url = `/monthly-balances?${params}`
    } else if (viewMode === 'current') {
      const params = new URLSearchParams({ group_by: 'account', currency: settings.currency })
      url = `/asset-values?${params}`
    } else {
      // monthly mode but no month selected yet
      setLoading(false)
      return
    }

    fetch(url, { signal: controller.signal })
      .then(r => r.json())
      .then(data => { setGroups(data); setLoading(false) })
      .catch((err) => {
        if (err.name === 'AbortError') return
        setError(true); setLoading(false)
      })

    return () => controller.abort()
  }, [viewMode, selectedMonth, settings.currency])

  const grandTotal = groups.reduce((sum, g) => sum + g.total_market_value, 0)

  return (
    <div>
      {/* View mode toggle + month selector */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', border: '1px solid var(--border)' }}>
          <button
            className={`hbtn${viewMode === 'current' ? ' active' : ''}`}
            onClick={() => setViewMode('current')}
            style={{ borderRadius: 0, borderRight: '1px solid var(--border)' }}
          >
            {s.assets.viewCurrent}
          </button>
          <button
            className={`hbtn${viewMode === 'monthly' ? ' active' : ''}`}
            onClick={() => setViewMode('monthly')}
            style={{ borderRadius: 0 }}
          >
            {s.assets.viewMonthly}
          </button>
        </div>

        {viewMode === 'monthly' && months.length > 0 && (
          <select
            value={selectedMonth}
            onChange={e => setSelectedMonth(e.target.value)}
            style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)' }}
          >
            {months.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        )}
      </div>

      {loading && <div className="state-msg">{s.common.loading}</div>}
      {error && <div className="state-msg" style={{ color: 'var(--red)' }}>{s.common.error}</div>}

      {!loading && !error && (
        <>
          <div className="card" style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ fontSize: 18 }}>{s.assets.total}</h2>
              <span style={{ fontSize: 24, fontWeight: 700 }}>
                {fmtMv(grandTotal)}
              </span>
            </div>
          </div>

          {groups.length === 0 && <div className="state-msg">{s.assets.noData}</div>}

          {groups.map(group => (
            <div key={group.group_key} className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <h3>{group.group_key}</h3>
                <span>
                  {fmtMv(group.total_market_value)}
                </span>
              </div>

              {group.stocks.length > 0 && (
                <>
                  <h4 style={{ marginBottom: 8, color: 'var(--text-muted)' }}>{s.assets.stocks}</h4>
                  <PositionTable positions={group.stocks} s={s} />
                </>
              )}
              {group.options.length > 0 && (
                <>
                  <h4 style={{ marginBottom: 8, marginTop: 12, color: 'var(--text-muted)' }}>
                    {s.assets.options}
                  </h4>
                  <PositionTable positions={group.options} s={s} />
                </>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  )
}
