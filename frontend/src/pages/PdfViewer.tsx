import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { pdfjsLib } from '../pdfjs'
import type { DoclingDocument } from '../types/docling'

interface OverlayBox {
  ref: string
  pageNo: number
  left: number
  top: number
  width: number
  height: number
  text: string
}

interface Props {
  pdfUrl: string
  doclingJson: DoclingDocument | null
  highlightedRefs: string[]
  onBoxClick: (ref: string) => void
}

function bboxToScreen(
  bbox: { l: number; t: number; r: number; b: number; coord_origin: string },
  pageHeight: number,
  scale: number,
): { left: number; top: number; width: number; height: number } {
  if (bbox.coord_origin === 'TOPLEFT') {
    return {
      left: bbox.l * scale,
      top: bbox.t * scale,
      width: (bbox.r - bbox.l) * scale,
      height: (bbox.b - bbox.t) * scale,
    }
  }
  // BOTTOMLEFT (default for texts)
  return {
    left: bbox.l * scale,
    top: (pageHeight - bbox.t) * scale,
    width: (bbox.r - bbox.l) * scale,
    height: (bbox.t - bbox.b) * scale,
  }
}

function buildOverlayBoxes(docling: DoclingDocument, scale: number): OverlayBox[] {
  const boxes: OverlayBox[] = []

  docling.texts?.forEach((text, i) => {
    text.prov?.forEach(prov => {
      const page = docling.pages[String(prov.page_no)]
      if (!page) return
      const rect = bboxToScreen(prov.bbox, page.size.height, scale)
      boxes.push({ ref: `#/texts/${i}`, pageNo: prov.page_no, text: text.text, ...rect })
    })
  })

  docling.tables?.forEach((table, ti) => {
    // Table-level box
    table.prov?.forEach(prov => {
      const page = docling.pages[String(prov.page_no)]
      if (!page) return
      const rect = bboxToScreen(prov.bbox, page.size.height, scale)
      boxes.push({
        ref: `#/tables/${ti}`,
        pageNo: prov.page_no,
        text: `Table (${table.data?.num_rows}r x ${table.data?.num_cols}c)`,
        ...rect,
      })
    })

    // Individual cells
    table.data?.table_cells?.forEach((cell, ci) => {
      if (!cell.bbox) return
      const pageNo = table.prov?.[0]?.page_no ?? 1
      const page = docling.pages[String(pageNo)]
      if (!page) return
      const rect = bboxToScreen(cell.bbox, page.size.height, scale)
      boxes.push({
        ref: `#/tables/${ti}/cells/${ci}`,
        pageNo,
        text: cell.text,
        ...rect,
      })
    })
  })

  docling.pictures?.forEach((pic, i) => {
    pic.prov?.forEach(prov => {
      const page = docling.pages[String(prov.page_no)]
      if (!page) return
      const rect = bboxToScreen(prov.bbox, page.size.height, scale)
      boxes.push({ ref: `#/pictures/${i}`, pageNo: prov.page_no, text: 'Image', ...rect })
    })
  })

  return boxes
}

export function PdfViewer({ pdfUrl, doclingJson, highlightedRefs, onBoxClick }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRefs = useRef<Record<number, HTMLCanvasElement | null>>({})
  const [numPages, setNumPages] = useState(0)
  const [scale, setScale] = useState(1.2)
  const [pageSizes, setPageSizes] = useState<Record<number, { w: number; h: number }>>({})
  const pdfRef = useRef<any>(null)

  // Load PDF
  useEffect(() => {
    if (!pdfUrl) return
    let cancelled = false

    pdfjsLib.getDocument(pdfUrl).promise.then(pdf => {
      if (cancelled) return
      pdfRef.current = pdf
      setNumPages(pdf.numPages)
    }).catch(() => {})

    return () => { cancelled = true }
  }, [pdfUrl])

  // Render pages
  useEffect(() => {
    const pdf = pdfRef.current
    if (!pdf || numPages === 0) return

    const sizes: Record<number, { w: number; h: number }> = {}

    for (let p = 1; p <= numPages; p++) {
      pdf.getPage(p).then((page: any) => {
        const viewport = page.getViewport({ scale })
        const canvas = canvasRefs.current[p]
        if (!canvas) return
        canvas.width = viewport.width
        canvas.height = viewport.height
        sizes[p] = { w: viewport.width, h: viewport.height }
        const ctx = canvas.getContext('2d')
        if (ctx) {
          page.render({ canvasContext: ctx, viewport })
        }
        if (p === numPages) setPageSizes({ ...sizes })
      })
    }
  }, [numPages, scale])

  // Build overlay boxes
  const boxes = useMemo(() => {
    if (!doclingJson) return []
    return buildOverlayBoxes(doclingJson, scale)
  }, [doclingJson, scale])

  const boxesByPage = useMemo(() => {
    const map: Record<number, OverlayBox[]> = {}
    for (const box of boxes) {
      if (!map[box.pageNo]) map[box.pageNo] = []
      map[box.pageNo].push(box)
    }
    return map
  }, [boxes])

  // Build a Set for fast lookup
  const highlightedSet = useMemo(() => new Set(highlightedRefs), [highlightedRefs])

  // Auto-scroll to first highlighted box
  useEffect(() => {
    if (highlightedRefs.length === 0 || !containerRef.current) return
    const el = containerRef.current.querySelector(`[data-ref="${CSS.escape(highlightedRefs[0])}"]`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [highlightedRefs])

  const handleScaleChange = useCallback((delta: number) => {
    setScale(s => Math.max(0.5, Math.min(3, s + delta)))
  }, [])

  if (!pdfUrl) return null

  return (
    <div className="pdf-viewer">
      <div className="pdf-controls">
        <button onClick={() => handleScaleChange(-0.2)} title="Zoom out">−</button>
        <span>{Math.round(scale * 100)}%</span>
        <button onClick={() => handleScaleChange(0.2)} title="Zoom in">+</button>
      </div>
      <div className="pdf-scroll-container" ref={containerRef}>
        {Array.from({ length: numPages }, (_, i) => i + 1).map(pageNum => (
          <div key={pageNum} className="pdf-page-wrapper">
            <div className="pdf-page-label">Page {pageNum}</div>
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <canvas ref={el => { canvasRefs.current[pageNum] = el }} />
              <div className="pdf-overlay">
                {boxesByPage[pageNum]?.map(box => (
                  <div
                    key={box.ref}
                    data-ref={box.ref}
                    className={`overlay-box${highlightedSet.has(box.ref) ? ' highlighted' : ''}`}
                    style={{
                      left: box.left,
                      top: box.top,
                      width: box.width,
                      height: box.height,
                    }}
                    onClick={(e) => { e.stopPropagation(); onBoxClick(box.ref) }}
                    title={box.text.substring(0, 100)}
                  />
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
