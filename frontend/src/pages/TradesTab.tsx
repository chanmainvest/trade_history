import { useState, useEffect, useMemo, useRef } from 'react'
import type { GlobalSettings } from '../App'
import type { Strings } from '../i18n'
import type { StatementNavigation } from './StatementsTab'

interface Trade {
  id: number
  institution: string
  account_id: string
  trade_date: string
  activity: string
  symbol: string | null
  asset_type: string | null
  quantity: number | null
  price: number | null
  amount: number
  currency: string
  commission: number | null
  source_file: string
  statement_id: number | null
}

interface Props {
  settings: GlobalSettings
  s: Strings
  onNavigateToStatement?: (nav: StatementNavigation) => void
  highlightTradeId?: number | null
}

const ACTIVITIES = [
  'bought', 'sold', 'dividend', 'interest', 'fee', 'exercise',
  'assignment', 'expired', 'transfer_in', 'transfer_out', 'contribution',
  'withdrawal', 'reinvestment', 'withholding_tax', 'journalled',
  'exchange', 'stock_split', 'adjustment', 'mark_to_market',
  'return_of_capital', 'cash_in_lieu', 'name_change', 'fx_conversion',
  'corporate_action', 'initial_holding', 'other',
]

const ASSET_TYPES = ['equity', 'option', 'mutual_fund', 'etf']
const PAGE_SIZES = [100, 200, 500, 1000, 2000]

function fmt(n: number | null, currency = ''): string {
  if (n === null || n === undefined) return '—'
  const s = Math.abs(n).toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `${n < 0 ? '-' : ''}${currency}${s}`
}

function badgeClass(activity: string): string {
  if (['bought', 'exercise', 'transfer_in', 'initial_holding'].includes(activity)) return 'badge badge-bought'
  if (['sold', 'assignment', 'expired', 'transfer_out'].includes(activity)) return 'badge badge-sold'
  if (['dividend', 'interest', 'reinvestment', 'return_of_capital'].includes(activity)) return 'badge badge-dividend'
  return 'badge badge-other'
}

export function TradesTab({ settings, s, onNavigateToStatement, highlightTradeId }: Props) {
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [totalCount, setTotalCount] = useState(0)
  const [retryKey, setRetryKey] = useState(0)

  const [symbolFilter, setSymbolFilter] = useState('')
  const [activityFilter, setActivityFilter] = useState('')
  const [assetFilter, setAssetFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [sortBy, setSortBy] = useState<keyof Trade>('trade_date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const [pageSize, setPageSize] = useState(200)
  const [page, setPage] = useState(1)

  const highlightRef = useRef<HTMLTableRowElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Only send date params when they're valid (YYYY-MM-DD)
  const validDateFrom = /^\d{4}-\d{2}-\d{2}$/.test(dateFrom) ? dateFrom : ''
  const validDateTo = /^\d{4}-\d{2}-\d{2}$/.test(dateTo) ? dateTo : ''

  useEffect(() => {
    // Abort any in-flight request
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(false)

    const params = new URLSearchParams({
      currency: settings.currency,
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
      sort_by: sortBy === 'trade_date' ? 'trade_date' : sortBy,
      sort_dir: sortDir,
    })
    if (symbolFilter) params.set('symbol', symbolFilter)
    if (activityFilter) params.set('activity', activityFilter)
    if (assetFilter) params.set('asset_type', assetFilter)
    if (validDateFrom) params.set('date_from', validDateFrom)
    if (validDateTo) params.set('date_to', validDateTo)

    fetch(`/trades?${params}`, { signal: controller.signal })
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then((data) => {
        setTrades(data)
        setLoading(false)
      })
      .catch((err) => {
        if (err.name === 'AbortError') return
        setError(true)
        setLoading(false)
      })

    return () => controller.abort()
  }, [settings.currency, pageSize, page, sortBy, sortDir, symbolFilter, activityFilter, assetFilter, validDateFrom, validDateTo, retryKey])

  // Fetch count for pagination
  useEffect(() => {
    const controller = new AbortController()

    const params = new URLSearchParams({ currency: settings.currency })
    if (symbolFilter) params.set('symbol', symbolFilter)
    if (activityFilter) params.set('activity', activityFilter)
    if (assetFilter) params.set('asset_type', assetFilter)
    if (validDateFrom) params.set('date_from', validDateFrom)
    if (validDateTo) params.set('date_to', validDateTo)

    fetch(`/trades/count?${params}`, { signal: controller.signal })
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then((data) => setTotalCount(data.count))
      .catch(() => {})

    return () => controller.abort()
  }, [settings.currency, symbolFilter, activityFilter, assetFilter, validDateFrom, validDateTo])

  // Reset to page 1 when filters change
  useEffect(() => { setPage(1) }, [symbolFilter, activityFilter, assetFilter, validDateFrom, validDateTo, pageSize])

  // Scroll to highlighted trade row
  useEffect(() => {
    if (highlightTradeId && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [highlightTradeId, trades])

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize))

  const sorted = useMemo(() => {
    // Server already sorts, but keep client sort for immediate feedback on current page
    return [...trades].sort((a, b) => {
      const av = a[sortBy] ?? ''
      const bv = b[sortBy] ?? ''
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
  }, [trades, sortBy, sortDir])

  function handleSort(col: keyof Trade) {
    if (col === sortBy) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortBy(col); setSortDir('desc') }
  }

  function handleRowClick(trade: Trade) {
    if (trade.statement_id && onNavigateToStatement) {
      onNavigateToStatement({
        statementId: trade.statement_id,
        tradeId: trade.id,
        sourceFile: trade.source_file,
      })
    }
  }

  function SortIcon({ col }: { col: keyof Trade }) {
    if (col !== sortBy) return <span style={{ opacity: 0.3 }}> ↕</span>
    return <span> {sortDir === 'asc' ? '↑' : '↓'}</span>
  }

  return (
    <div>
      <div className="filters">
        <input
          placeholder={s.trades.filters.search}
          value={symbolFilter}
          onChange={e => setSymbolFilter(e.target.value)}
        />
        <select value={activityFilter} onChange={e => setActivityFilter(e.target.value)}>
          <option value="">{s.trades.filters.activity}</option>
          {ACTIVITIES.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={assetFilter} onChange={e => setAssetFilter(e.target.value)}>
          <option value="">{s.trades.filters.assetType}</option>
          {ASSET_TYPES.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <div className="date-range">
          <label className="date-label">From</label>
          <input
            type="date"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
          />
          <label className="date-label">To</label>
          <input
            type="date"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
          />
        </div>
        <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
          {totalCount.toLocaleString()} rows
        </span>
      </div>

      {error && (
        <div style={{ color: 'var(--red)', padding: '8px 0', fontSize: '13px' }}>
          {s.common.error}
          <button
            style={{ marginLeft: 8, fontSize: 12, cursor: 'pointer', background: 'none', border: '1px solid var(--red)', borderRadius: 4, padding: '2px 8px', color: 'var(--red)' }}
            onClick={() => setRetryKey(k => k + 1)}
          >
            Retry
          </button>
        </div>
      )}

      <div className="card">
        {loading && <div className="state-msg" style={{ padding: '12px' }}>{s.common.loading}</div>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {[
                  { key: 'trade_date', label: s.trades.columns.date },
                  { key: 'institution', label: s.trades.columns.institution },
                  { key: 'account_id', label: s.trades.columns.account },
                  { key: 'activity', label: s.trades.columns.activity },
                  { key: 'symbol', label: s.trades.columns.symbol },
                  { key: 'asset_type', label: s.trades.columns.assetType },
                  { key: 'quantity', label: s.trades.columns.quantity },
                  { key: 'price', label: s.trades.columns.price },
                  { key: 'amount', label: s.trades.columns.amount },
                  { key: 'currency', label: s.trades.columns.currency },
                ].map(({ key, label }) => (
                  <th key={key} onClick={() => handleSort(key as keyof Trade)}>
                    {label}<SortIcon col={key as keyof Trade} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((trade) => (
                <tr
                  key={trade.id}
                  ref={trade.id === highlightTradeId ? highlightRef : null}
                  className={
                    (trade.id === highlightTradeId ? 'trade-highlighted ' : '') +
                    (trade.statement_id ? 'trade-clickable' : '')
                  }
                  onClick={() => handleRowClick(trade)}
                >
                  <td>{trade.trade_date}</td>
                  <td>{trade.institution}</td>
                  <td>{trade.account_id}</td>
                  <td><span className={badgeClass(trade.activity)}>{trade.activity}</span></td>
                  <td><strong>{trade.symbol ?? '—'}</strong></td>
                  <td>{trade.asset_type ?? '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    {trade.quantity !== null ? trade.quantity.toLocaleString() : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {fmt(trade.price)}
                  </td>
                  <td
                    style={{ textAlign: 'right' }}
                    className={trade.amount >= 0 ? 'positive' : 'negative'}
                  >
                    {fmt(trade.amount)}
                  </td>
                  <td>{trade.currency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="pagination">
          <div className="page-size">
            <label>Rows:</label>
            <select value={pageSize} onChange={e => setPageSize(Number(e.target.value))}>
              {PAGE_SIZES.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="page-nav">
            <button disabled={page <= 1} onClick={() => setPage(1)} title="First page">⟨⟨</button>
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} title="Previous page">⟨</button>
            <span className="page-info">
              Page {page} of {totalPages}
            </span>
            <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} title="Next page">⟩</button>
            <button disabled={page >= totalPages} onClick={() => setPage(totalPages)} title="Last page">⟩⟩</button>
          </div>
        </div>
      </div>
    </div>
  )
}
