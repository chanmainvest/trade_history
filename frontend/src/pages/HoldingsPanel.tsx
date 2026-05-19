import { useState, useEffect } from 'react'
import type { DoclingDocument } from '../types/docling'
import type { Strings } from '../i18n'

interface HoldingPosition {
  symbol: string
  asset_type: string
  quantity: number
  market_price: number | null
  market_value: number | null
  book_cost: number | null
  currency: string
}

interface CashBalances {
  opening_cad: number | null
  closing_cad: number | null
  opening_usd: number | null
  closing_usd: number | null
}

interface HoldingsData {
  positions: HoldingPosition[]
  cash: CashBalances
}

interface Props {
  statementId: number | null
  doclingJson: DoclingDocument | null
  highlightedRefs: string[]
  onHighlight: (refs: string[]) => void
  s: Strings
}

const TOKEN_RE = /[A-Za-z0-9.,$]+/g

function normalizeToken(t: string): string {
  let s = t.toLowerCase().replace(/[$,]/g, '')
  // Normalize trailing decimal zeros: "40.590" → "40.59", "2000.00" → "2000"
  if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '')
  return s
}

function tokenize(text: string): Set<string> {
  return new Set(
    (text.match(TOKEN_RE) || []).map(normalizeToken).filter(t => t.length > 0)
  )
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0
  let intersection = 0
  for (const t of a) if (b.has(t)) intersection++
  return intersection / (a.size + b.size - intersection)
}

function findMatchingRefs(searchText: string, docling: DoclingDocument): string[] {
  const tokens = tokenize(searchText)
  if (tokens.size === 0) return []

  let bestScore = 0
  let bestRefs: string[] = []

  docling.tables?.forEach((table, ti) => {
    const cells = table.data?.table_cells
    if (!cells) return

    const rowMap = new Map<number, { texts: string[], cellIndices: number[] }>()
    cells.forEach((cell, ci) => {
      const row = cell.start_row_offset_idx
      const existing = rowMap.get(row)
      if (!existing) {
        rowMap.set(row, { texts: [cell.text], cellIndices: cell.text.trim() ? [ci] : [] })
      } else {
        existing.texts.push(cell.text)
        if (cell.text.trim()) existing.cellIndices.push(ci)
      }
    })

    for (const [, { texts, cellIndices }] of rowMap) {
      const rowText = texts.join(' ')
      if (rowText.length < 5) continue
      const score = jaccard(tokens, tokenize(rowText))
      if (score > bestScore) {
        bestScore = score
        bestRefs = cellIndices.map(ci => `#/tables/${ti}/cells/${ci}`)
      }
    }
  })

  return bestScore >= 0.3 ? bestRefs : []
}

function fmtAmt(n: number | null): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtQty(n: number | null): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString('en-CA', { maximumFractionDigits: 0 })
}

export function HoldingsPanel({ statementId, doclingJson, highlightedRefs, onHighlight, s }: Props) {
  const [data, setData] = useState<HoldingsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)

  useEffect(() => {
    if (!statementId) {
      setData(null)
      return
    }
    setLoading(true)
    setSelectedIdx(null)
    fetch(`/statements/${statementId}/holdings`)
      .then(r => r.json())
      .then((d: HoldingsData) => {
        setData(d)
        setLoading(false)
      })
      .catch(() => {
        setData(null)
        setLoading(false)
      })
  }, [statementId])

  const handleClick = (pos: HoldingPosition, idx: number) => {
    setSelectedIdx(idx)

    if (!doclingJson) {
      onHighlight([])
      return
    }

    // Build search text from position fields for Jaccard matching
    const parts = [pos.symbol || '']
    if (pos.quantity != null) parts.push(pos.quantity.toString())
    if (pos.market_price != null) parts.push(pos.market_price.toFixed(2))
    if (pos.market_value != null) parts.push(fmtAmt(pos.market_value).replace(/,/g, ''))
    const searchText = parts.join(' ')

    const refs = findMatchingRefs(searchText, doclingJson)
    onHighlight(refs)
  }

  if (loading) {
    return <div className="state-msg">{s.common.loading}</div>
  }

  if (!data || (data.positions.length === 0 && !data.cash.closing_cad && !data.cash.closing_usd)) {
    return <div className="state-msg">{s.statements.noHoldings}</div>
  }

  const hasCash = data.cash.opening_cad != null || data.cash.closing_cad != null
    || data.cash.opening_usd != null || data.cash.closing_usd != null

  return (
    <div className="tx-panel">
      <div className="tx-header">
        <span className="json-summary">{data.positions.length} positions</span>
      </div>
      <div className="tx-entries">
        {data.positions.map((pos, i) => (
          <div
            key={`${pos.symbol}-${pos.currency}-${i}`}
            className={`holdings-row${i === selectedIdx ? ' highlighted' : ''}`}
            onClick={() => handleClick(pos, i)}
          >
            <span className="tx-symbol">{pos.symbol || '—'}</span>
            <span className="holdings-type">{pos.asset_type}</span>
            <span className="tx-qty">{fmtQty(pos.quantity)}</span>
            <span className="tx-amount">{fmtAmt(pos.market_price)}</span>
            <span className="tx-amount">{fmtAmt(pos.market_value)}</span>
            <span className="tx-ccy">{pos.currency}</span>
          </div>
        ))}

        {hasCash && (
          <div className="holdings-cash">
            <div className="holdings-cash-title">Cash</div>
            {(data.cash.opening_cad != null || data.cash.closing_cad != null) && (
              <div className="holdings-cash-row">
                <span>CAD</span>
                <span>Open: {fmtAmt(data.cash.opening_cad)}</span>
                <span>Close: {fmtAmt(data.cash.closing_cad)}</span>
              </div>
            )}
            {(data.cash.opening_usd != null || data.cash.closing_usd != null) && (
              <div className="holdings-cash-row">
                <span>USD</span>
                <span>Open: {fmtAmt(data.cash.opening_usd)}</span>
                <span>Close: {fmtAmt(data.cash.closing_usd)}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
