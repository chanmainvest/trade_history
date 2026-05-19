import { useState, useEffect, useRef } from 'react'
import type { DoclingDocument } from '../types/docling'
import type { Strings } from '../i18n'

interface StatementTransaction {
  id: number
  trade_date: string
  activity: string
  symbol: string | null
  asset_type: string | null
  quantity: number | null
  price: number | null
  amount: number
  currency: string
  raw_text: string | null
  docling_refs: string[]
  docling_page: number | null
}

interface Props {
  statementId: number | null
  doclingJson: DoclingDocument | null
  highlightedRefs: string[]
  onHighlight: (refs: string[]) => void
  s: Strings
}

const TOKEN_RE = /[A-Za-z0-9.,$]+/g

function tokenize(text: string): Set<string> {
  return new Set((text.match(TOKEN_RE) || []).map(t => t.toLowerCase()))
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0
  let intersection = 0
  for (const t of a) if (b.has(t)) intersection++
  return intersection / (a.size + b.size - intersection)
}

function findMatchingRefs(rawText: string, docling: DoclingDocument): string[] {
  const txTokens = tokenize(rawText)
  if (txTokens.size === 0) return []

  let bestScore = 0
  let bestRefs: string[] = []

  docling.tables?.forEach((table, ti) => {
    const cells = table.data?.table_cells
    if (!cells) return

    // Group cells by row — collect ALL cell indices per row
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
      const score = jaccard(txTokens, tokenize(rowText))
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

export function TransactionsPanel({ statementId, doclingJson, highlightedRefs, onHighlight, s }: Props) {
  const [transactions, setTransactions] = useState<StatementTransaction[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedTxId, setSelectedTxId] = useState<number | null>(null)
  const highlightedRowRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!statementId) {
      setTransactions([])
      return
    }
    setLoading(true)
    setSelectedTxId(null)
    fetch(`/statements/${statementId}/transactions`)
      .then(r => r.json())
      .then((data: StatementTransaction[]) => {
        setTransactions(data)
        setLoading(false)
      })
      .catch(() => {
        setTransactions([])
        setLoading(false)
      })
  }, [statementId])

  const handleClick = (tx: StatementTransaction) => {
    setSelectedTxId(tx.id)

    // Try docling_refs first, then client-side matching
    if (tx.docling_refs.length > 0) {
      onHighlight(tx.docling_refs)
      return
    }

    if (tx.raw_text && doclingJson) {
      const refs = findMatchingRefs(tx.raw_text, doclingJson)
      onHighlight(refs)
    } else {
      onHighlight([])
    }
  }

  if (loading) {
    return <div className="state-msg">{s.common.loading}</div>
  }

  if (transactions.length === 0) {
    return <div className="state-msg">{s.statements.noTransactions}</div>
  }

  return (
    <div className="tx-panel">
      <div className="tx-header">
        <span className="json-summary">{transactions.length} transactions</span>
      </div>
      <div className="tx-entries">
        {transactions.map(tx => (
          <div
            key={tx.id}
            ref={tx.id === selectedTxId ? highlightedRowRef : null}
            className={`tx-row${tx.id === selectedTxId ? ' highlighted' : ''}`}
            onClick={() => handleClick(tx)}
            title={tx.raw_text || undefined}
          >
            <span className="tx-date">{tx.trade_date}</span>
            <span className="tx-activity">{tx.activity}</span>
            <span className="tx-symbol">{tx.symbol || '—'}</span>
            <span className="tx-qty">{tx.quantity != null ? fmtAmt(tx.quantity) : '—'}</span>
            <span className="tx-amount">{fmtAmt(tx.amount)}</span>
            <span className="tx-ccy">{tx.currency}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
