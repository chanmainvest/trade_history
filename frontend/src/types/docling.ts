export interface DoclingBbox {
  l: number
  t: number
  r: number
  b: number
  coord_origin: 'BOTTOMLEFT' | 'TOPLEFT'
}

export interface DoclingProv {
  page_no: number
  bbox: DoclingBbox
  charspan: [number, number]
}

export interface DoclingText {
  self_ref: string
  label: string
  text: string
  prov: DoclingProv[]
}

export interface DoclingTableCell {
  bbox?: DoclingBbox
  text: string
  row_span: number
  col_span: number
  start_row_offset_idx: number
  end_row_offset_idx: number
  start_col_offset_idx: number
  end_col_offset_idx: number
}

export interface DoclingTableData {
  table_cells: DoclingTableCell[]
  num_rows: number
  num_cols: number
}

export interface DoclingTable {
  self_ref: string
  label: string
  prov: DoclingProv[]
  data: DoclingTableData
}

export interface DoclingPageSize {
  width: number
  height: number
}

export interface DoclingPage {
  size: DoclingPageSize
  page_no: number
}

export interface DoclingDocument {
  schema_name: string
  version: string
  name: string
  pages: Record<string, DoclingPage>
  texts: DoclingText[]
  tables: DoclingTable[]
  pictures?: Array<{ self_ref: string; label: string; prov: DoclingProv[] }>
  body?: { children: Array<{ $ref: string }> }
}
