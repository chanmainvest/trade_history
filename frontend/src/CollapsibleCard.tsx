import { useState } from "react";
import type { ReactNode } from "react";

// Card whose title toggles its body. Open by default; the toggle is local UI
// state and intentionally not persisted to preferences.
export function CollapsibleCard({ title, children }: { title: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="card">
      <h3 className="card-collapse-header">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className={"card-collapse-glyph" + (open ? " is-open" : "")} aria-hidden="true">▸</span>
          {title}
        </button>
      </h3>
      {open && children}
    </div>
  );
}
