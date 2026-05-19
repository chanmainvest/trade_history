import { useEffect, useRef, useMemo } from 'react'
import type { DoclingDocument } from '../types/docling'
import type { Strings } from '../i18n'

interface Props {
  doclingJson: DoclingDocument | null
  highlightedRefs: string[]
  onEntryClick: (ref: string) => void
  viewMode: 'structured' | 'raw'
  s: Strings
}

interface JsonEntry {
  ref: string
  label: string
  text: string
  pageNo: number
}

function buildEntries(doc: DoclingDocument): JsonEntry[] {
  const entries: JsonEntry[] = []

  doc.texts?.forEach((t, i) => {
    entries.push({
      ref: `#/texts/${i}`,
      label: t.label || 'text',
      text: t.text,
      pageNo: t.prov?.[0]?.page_no ?? 0,
    })
  })

  doc.tables?.forEach((t, ti) => {
    entries.push({
      ref: `#/tables/${ti}`,
      label: 'table',
      text: `Table (${t.data?.num_rows}r x ${t.data?.num_cols}c)`,
      pageNo: t.prov?.[0]?.page_no ?? 0,
    })

    t.data?.table_cells?.forEach((cell, ci) => {
      if (!cell.text.trim()) return
      entries.push({
        ref: `#/tables/${ti}/cells/${ci}`,
        label: `r${cell.start_row_offset_idx}c${cell.start_col_offset_idx}`,
        text: cell.text,
        pageNo: t.prov?.[0]?.page_no ?? 0,
      })
    })
  })

  doc.pictures?.forEach((p, i) => {
    entries.push({
      ref: `#/pictures/${i}`,
      label: 'picture',
      text: 'Image',
      pageNo: p.prov?.[0]?.page_no ?? 0,
    })
  })

  return entries
}

export function DoclingJsonViewer({ doclingJson, highlightedRefs, onEntryClick, viewMode, s }: Props) {
  const highlightedEntryRef = useRef<HTMLDivElement>(null)
  const rawRef = useRef<HTMLPreElement>(null)

  const entries = doclingJson ? buildEntries(doclingJson) : []
  const highlightedSet = useMemo(() => new Set(highlightedRefs), [highlightedRefs])

  // Auto-scroll to first highlighted entry in structured view
  useEffect(() => {
    if (highlightedRefs.length > 0 && highlightedEntryRef.current && viewMode === 'structured') {
      highlightedEntryRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [highlightedRefs, viewMode])

  // Auto-scroll in raw view
  useEffect(() => {
    if (highlightedRefs.length > 0 && viewMode === 'raw' && rawRef.current) {
      const text = rawRef.current.textContent || ''
      const idx = text.indexOf(highlightedRefs[0])
      if (idx >= 0) {
        const lineHeight = 16
        const charPerLine = 80
        const lineNum = Math.floor(idx / charPerLine)
        rawRef.current.scrollTop = lineNum * lineHeight - 100
      }
    }
  }, [highlightedRefs, viewMode])

  if (!doclingJson) {
    return <div className="state-msg">{s.statements.noDocling}</div>
  }

  return (
    <div className="json-viewer">
      <div className="json-viewer-header">
        <span className="json-summary">
          {doclingJson.texts?.length ?? 0} {s.statements.texts},
          {' '}{doclingJson.tables?.length ?? 0} {s.statements.tables}
        </span>
      </div>

      {viewMode === 'structured' ? (
        <div className="json-entries">
          {entries.map(entry => {
            const isHighlighted = highlightedSet.has(entry.ref)
            return (
            <div
              key={entry.ref}
              ref={isHighlighted ? highlightedEntryRef : null}
              className={`json-entry${isHighlighted ? ' highlighted' : ''}`}
              onClick={() => onEntryClick(entry.ref)}
            >
              <span className="json-ref">{entry.ref}</span>
              <span className="json-label">[{entry.label}]</span>
              <span className="json-text">
                {entry.text.substring(0, 80)}{entry.text.length > 80 ? '…' : ''}
              </span>
              <span className="json-page">p.{entry.pageNo}</span>
            </div>
            )
          })}
        </div>
      ) : (
        <pre className="json-raw" ref={rawRef}>
          {JSON.stringify(doclingJson, null, 2)}
        </pre>
      )}
    </div>
  )
}
