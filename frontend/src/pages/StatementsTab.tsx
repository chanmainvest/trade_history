import { useState, useEffect, useCallback } from 'react'
import type { GlobalSettings } from '../App'
import type { Strings } from '../i18n'
import type { DoclingDocument } from '../types/docling'
import { PdfViewer } from './PdfViewer'
import { DoclingJsonViewer } from './DoclingJsonViewer'
import { TransactionsPanel } from './TransactionsPanel'
import { HoldingsPanel } from './HoldingsPanel'

export interface StatementNavigation {
  statementId: number
  tradeId?: number
  sourceFile?: string
}

interface AccountOption {
  institution: string
  account_id: string
  group_key: string
  statement_count: number
}

interface StatementSummary {
  id: number
  source_file: string
  institution: string
  account_id: string
  period_start: string
  period_end: string
  status: string
  transaction_count: number
  has_docling_json: boolean
}

interface StatementDetail {
  id: number
  source_file: string
  institution: string
  account_id: string
  period_start: string
  period_end: string
  status: string
  transaction_count: number
  docling_json: DoclingDocument | null
}

type RightTab = 'transactions' | 'holdings' | 'structured' | 'raw'

interface Props {
  settings: GlobalSettings
  s: Strings
  initialNav: StatementNavigation | null
  onNavigateToTrade: (tradeId: number) => void
}

export function StatementsTab({ settings, s, initialNav, onNavigateToTrade }: Props) {
  const [accounts, setAccounts] = useState<AccountOption[]>([])
  const [selectedAccount, setSelectedAccount] = useState('')
  const [statements, setStatements] = useState<StatementSummary[]>([])
  const [selectedStatementId, setSelectedStatementId] = useState<number | null>(null)
  const [statementDetail, setStatementDetail] = useState<StatementDetail | null>(null)
  const [highlightedRefs, setHighlightedRefs] = useState<string[]>([])
  const [originTradeId, setOriginTradeId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [navProcessed, setNavProcessed] = useState(false)
  const [rightTab, setRightTab] = useState<RightTab>('transactions')

  // Fetch accounts on mount
  useEffect(() => {
    fetch('/statements/accounts')
      .then(r => r.json())
      .then(setAccounts)
      .catch(() => {})
  }, [])

  // Handle initialNav — auto-select account and statement
  useEffect(() => {
    if (!initialNav || navProcessed || accounts.length === 0) return

    setNavProcessed(true)
    setOriginTradeId(initialNav.tradeId ?? null)

    // Load the statement detail directly
    setLoading(true)
    fetch(`/statements/${initialNav.statementId}`)
      .then(r => r.json())
      .then((detail: StatementDetail) => {
        setStatementDetail(detail)
        setSelectedStatementId(detail.id)

        // Set the account dropdown
        const groupKey = `${detail.institution} | ${detail.account_id}`
        setSelectedAccount(groupKey)

        // Load statements for this account to populate dropdown
        const params = new URLSearchParams({
          institution: detail.institution,
          account_id: detail.account_id,
        })
        fetch(`/statements?${params}`)
          .then(r => r.json())
          .then(setStatements)
          .catch(() => {})

        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [initialNav, navProcessed, accounts])

  // Fetch statements when account changes (user selection)
  const handleAccountChange = useCallback((groupKey: string) => {
    setSelectedAccount(groupKey)
    setSelectedStatementId(null)
    setStatementDetail(null)
    setHighlightedRefs([])

    if (!groupKey) {
      setStatements([])
      return
    }

    const account = accounts.find(a => a.group_key === groupKey)
    if (!account) return

    const params = new URLSearchParams({
      institution: account.institution,
      account_id: account.account_id,
    })
    fetch(`/statements?${params}`)
      .then(r => r.json())
      .then(setStatements)
      .catch(() => {})
  }, [accounts])

  // Fetch statement detail when statement changes
  const handleStatementChange = useCallback((idStr: string) => {
    const id = parseInt(idStr, 10)
    if (isNaN(id)) {
      setSelectedStatementId(null)
      setStatementDetail(null)
      setHighlightedRefs([])
      return
    }

    setSelectedStatementId(id)
    setHighlightedRefs([])
    setLoading(true)

    fetch(`/statements/${id}`)
      .then(r => r.json())
      .then((detail: StatementDetail) => {
        setStatementDetail(detail)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  const pdfUrl = selectedStatementId ? `/statements/${selectedStatementId}/pdf` : ''

  return (
    <div className="statements-page">
      <div className="statements-toolbar">
        <select
          value={selectedAccount}
          onChange={e => handleAccountChange(e.target.value)}
        >
          <option value="">{s.statements.selectAccount}</option>
          {accounts.map(a => (
            <option key={a.group_key} value={a.group_key}>
              {a.group_key} ({a.statement_count})
            </option>
          ))}
        </select>

        <select
          value={selectedStatementId ?? ''}
          onChange={e => handleStatementChange(e.target.value)}
          disabled={statements.length === 0}
        >
          <option value="">{s.statements.selectStatement}</option>
          {statements.map(st => (
            <option key={st.id} value={st.id}>
              {st.period_start} — {st.period_end}
              {st.has_docling_json ? '' : ' (no data)'}
            </option>
          ))}
        </select>

        {originTradeId && (
          <button
            className="hbtn"
            onClick={() => onNavigateToTrade(originTradeId)}
          >
            {s.statements.backToTrade}
          </button>
        )}

        {loading && <span style={{ color: 'var(--text-muted)' }}>{s.common.loading}</span>}
      </div>

      {selectedStatementId && (
        <div className="statements-split">
          <div className="statements-pdf-pane">
            <PdfViewer
              pdfUrl={pdfUrl}
              doclingJson={statementDetail?.docling_json ?? null}
              highlightedRefs={highlightedRefs}
              onBoxClick={(ref) => setHighlightedRefs([ref])}
            />
          </div>
          <div className="statements-json-pane">
            <div className="right-panel-tabs">
              <button
                className={rightTab === 'transactions' ? 'active' : ''}
                onClick={() => setRightTab('transactions')}
              >
                {s.statements.viewTransactions}
              </button>
              <button
                className={rightTab === 'holdings' ? 'active' : ''}
                onClick={() => setRightTab('holdings')}
              >
                {s.statements.viewHoldings}
              </button>
              <button
                className={rightTab === 'structured' ? 'active' : ''}
                onClick={() => setRightTab('structured')}
              >
                {s.statements.viewStructured}
              </button>
              <button
                className={rightTab === 'raw' ? 'active' : ''}
                onClick={() => setRightTab('raw')}
              >
                {s.statements.viewRaw}
              </button>
            </div>

            {rightTab === 'transactions' ? (
              <TransactionsPanel
                statementId={selectedStatementId}
                doclingJson={statementDetail?.docling_json ?? null}
                highlightedRefs={highlightedRefs}
                onHighlight={setHighlightedRefs}
                s={s}
              />
            ) : rightTab === 'holdings' ? (
              <HoldingsPanel
                statementId={selectedStatementId}
                doclingJson={statementDetail?.docling_json ?? null}
                highlightedRefs={highlightedRefs}
                onHighlight={setHighlightedRefs}
                s={s}
              />
            ) : (
              <DoclingJsonViewer
                doclingJson={statementDetail?.docling_json ?? null}
                highlightedRefs={highlightedRefs}
                onEntryClick={(ref) => setHighlightedRefs([ref])}
                viewMode={rightTab as 'structured' | 'raw'}
                s={s}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}
