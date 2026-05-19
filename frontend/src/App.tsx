import { useState, useCallback } from 'react'
import { TradesTab } from './pages/TradesTab'
import { AssetValueTab } from './pages/AssetValueTab'
import { SectorsTab } from './pages/SectorsTab'
import { StatementsTab, type StatementNavigation } from './pages/StatementsTab'
import { t, type Language } from './i18n'
import './App.css'

type Tab = 'trades' | 'assets' | 'sectors' | 'statements'

export interface GlobalSettings {
  currency: 'CAD' | 'USD'
  language: Language
}

export default function App() {
  const [tab, setTab] = useState<Tab>('trades')
  const [settings, setSettings] = useState<GlobalSettings>({
    currency: 'CAD',
    language: 'en',
  })
  const [statementNav, setStatementNav] = useState<StatementNavigation | null>(null)
  const [highlightTradeId, setHighlightTradeId] = useState<number | null>(null)

  const s = t(settings.language)

  const handleNavigateToStatement = useCallback((nav: StatementNavigation) => {
    setStatementNav(nav)
    setTab('statements')
  }, [])

  const handleNavigateToTrade = useCallback((tradeId: number) => {
    setStatementNav(null)
    setHighlightTradeId(tradeId)
    setTab('trades')
  }, [])

  return (
    <div className="app">
      <header className="header">
        <h1>Trade History</h1>
        <nav className="tabs">
          {(['trades', 'assets', 'sectors', 'statements'] as Tab[]).map((tabKey) => (
            <button
              key={tabKey}
              className={tab === tabKey ? 'tab active' : 'tab'}
              onClick={() => { setTab(tabKey); if (tabKey === 'statements') setStatementNav(null) }}
            >
              {s.tabs[tabKey]}
            </button>
          ))}
        </nav>

        <div className="header-controls">
          <div className="header-toggle">
            <button
              className={settings.currency === 'CAD' ? 'htoggle active' : 'htoggle'}
              onClick={() => setSettings({ ...settings, currency: 'CAD' })}
            >CAD</button>
            <button
              className={settings.currency === 'USD' ? 'htoggle active' : 'htoggle'}
              onClick={() => setSettings({ ...settings, currency: 'USD' })}
            >USD</button>
          </div>

          <div className="header-toggle">
            <button
              className={settings.language === 'en' ? 'htoggle active' : 'htoggle'}
              onClick={() => setSettings({ ...settings, language: 'en' })}
            >EN</button>
            <button
              className={settings.language === 'zh-TW' ? 'htoggle active' : 'htoggle'}
              onClick={() => setSettings({ ...settings, language: 'zh-TW' })}
            >繁</button>
          </div>

        </div>
      </header>

      <main className={`content${tab === 'statements' ? ' wide' : ''}`}>
        {tab === 'trades' && (
          <TradesTab
            settings={settings}
            s={s}
            onNavigateToStatement={handleNavigateToStatement}
            highlightTradeId={highlightTradeId}
          />
        )}
        {tab === 'assets' && <AssetValueTab settings={settings} s={s} />}
        {tab === 'sectors' && <SectorsTab settings={settings} s={s} />}
        {tab === 'statements' && (
          <StatementsTab
            settings={settings}
            s={s}
            initialNav={statementNav}
            onNavigateToTrade={handleNavigateToTrade}
          />
        )}
      </main>
    </div>
  )
}
