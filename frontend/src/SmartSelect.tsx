import { useEffect, useMemo, useRef, useState } from "react";

export type SmartOption = { value: string; label: string; hint?: string };

export function SmartSelect(props: {
  label: string;
  options: SmartOption[];
  value: string[];                       // selected values
  onChange: (v: string[]) => void;
  placeholder?: string;
  width?: number | string;
}) {
  const { label, options, value, onChange } = props;
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return options;
    return options.filter((o) =>
      o.label.toLowerCase().includes(s) ||
      o.value.toLowerCase().includes(s) ||
      (o.hint ? o.hint.toLowerCase().includes(s) : false)
    );
  }, [q, options]);

  const triggerText = value.length === 0
    ? (props.placeholder || `All ${label.toLowerCase()}`)
    : value.length === 1
      ? (options.find((o) => o.value === value[0])?.label || value[0])
      : `${value.length} selected`;

  function toggle(v: string) {
    if (value.includes(v)) onChange(value.filter((x) => x !== v));
    else onChange([...value, v]);
  }
  function selectAll() { onChange(filtered.map((o) => o.value)); }
  function clear() { onChange([]); }

  return (
    <div className="smart-select" ref={wrap} style={{ width: props.width }}>
      <button type="button" className="trigger" onClick={() => setOpen(!open)}>
        <span className="muted">{label}:</span> {triggerText}
        {value.length > 0 && <span className="badge">{value.length}</span>}
      </button>
      {open && (
        <div className="panel">
          <input
            className="search" autoFocus
            placeholder="Type to search…"
            value={q} onChange={(e) => setQ(e.target.value)}
          />
          <div className="actions">
            <button type="button" onClick={selectAll}>Select shown</button>
            <button type="button" onClick={clear}>Clear</button>
            <span style={{ flex: 1 }} />
            <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>
              {filtered.length}/{options.length}
            </span>
          </div>
          <div className="options">
            {filtered.length === 0 && <div className="empty">No matches.</div>}
            {filtered.map((o) => (
              <label key={o.value}>
                <input
                  type="checkbox" checked={value.includes(o.value)}
                  onChange={() => toggle(o.value)}
                />
                <span style={{ flex: 1 }}>{o.label}</span>
                {o.hint && <span className="muted" style={{ fontSize: 11 }}>{o.hint}</span>}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
