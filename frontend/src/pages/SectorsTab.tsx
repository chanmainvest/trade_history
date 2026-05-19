import { useState, useEffect } from 'react'
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid } from 'recharts'
import type { GlobalSettings } from '../App'
import type { Strings } from '../i18n'

interface SectorRow {
  sector: string
  market_value: number
  percentage: number
}

interface Props {
  settings: GlobalSettings
  s: Strings
}

const COLORS = [
  '#1a6fad', '#16a34a', '#dc2626', '#d97706', '#7c3aed',
  '#0891b2', '#be185d', '#65a30d', '#f97316', '#6366f1',
]

export function SectorsTab({ settings, s }: Props) {
  const [data, setData] = useState<SectorRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [chartType, setChartType] = useState<'pie' | 'bar'>('pie')

  useEffect(() => {
    setLoading(true)
    fetch('/sectors')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [])

  if (loading) return <div className="state-msg">{s.common.loading}</div>
  if (error) return <div className="state-msg" style={{ color: 'var(--red)' }}>{s.common.error}</div>
  if (data.length === 0) return <div className="state-msg">{s.sectors.noData}</div>

  const fmtLabel = ({ sector, percentage }: SectorRow) =>
    `${sector} (${percentage.toFixed(1)}%)`

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <button
          className={chartType === 'pie' ? 'toggle-btn active' : 'toggle-btn'}
          onClick={() => setChartType('pie')}
        >
          Pie
        </button>
        <button
          className={chartType === 'bar' ? 'toggle-btn active' : 'toggle-btn'}
          onClick={() => setChartType('bar')}
        >
          Bar
        </button>
      </div>

      <div className="card">
        <h2 style={{ marginBottom: 20 }}>{s.sectors.title}</h2>

        {chartType === 'pie' ? (
          <ResponsiveContainer width="100%" height={420}>
            <PieChart>
              <Pie
                data={data}
                dataKey="percentage"
                nameKey="sector"
                cx="50%"
                cy="50%"
                outerRadius={160}
                label={({ sector, percentage }) =>
                  `${percentage.toFixed(1)}%`
                }
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value: number) =>
                  `${value.toFixed(2)}%`
                }
              />
              <Legend formatter={(value) => value} />
            </PieChart>
          </ResponsiveContainer>
        ) : (
          <ResponsiveContainer width="100%" height={420}>
            <BarChart data={data} layout="vertical" margin={{ left: 160, right: 32 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" unit="%" domain={[0, 100]} />
              <YAxis type="category" dataKey="sector" width={150} />
              <Tooltip
                formatter={(value: number) =>
                  `${value.toFixed(2)}%`
                }
              />
              <Bar dataKey="percentage" fill="#1a6fad" />
            </BarChart>
          </ResponsiveContainer>
        )}

        <table style={{ marginTop: 24 }}>
          <thead>
            <tr>
              <th>Sector</th>
              <th style={{ textAlign: 'right' }}>Market Value</th>
              <th style={{ textAlign: 'right' }}>%</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i}>
                <td>
                  <span style={{
                    display: 'inline-block',
                    width: 10,
                    height: 10,
                    borderRadius: '50%',
                    background: COLORS[i % COLORS.length],
                    marginRight: 8,
                  }} />
                  {row.sector}
                </td>
                <td style={{ textAlign: 'right' }}>
                  {row.market_value.toLocaleString('en-CA', { style: 'currency', currency: 'CAD' })}
                </td>
                <td style={{ textAlign: 'right' }}>{row.percentage.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
