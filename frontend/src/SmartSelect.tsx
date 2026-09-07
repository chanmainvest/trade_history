import { useEffect, useMemo, useRef, useState } from "react";

export type SmartOption = { value: string; label: string; hint?: string };

function useOptionFilter(options: SmartOption[], q: string) {
  return useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return options;
    return options.filter((o) =>
      o.label.toLowerCase().includes(s) ||
      o.value.toLowerCase().includes(s) ||
      (o.hint ? o.hint.toLowerCase().includes(s) : false)
    );
  }, [q, options]);
}

function OptionPanel(props: {
  options: SmartOption[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const filtered = useOptionFilter(props.options, q);
  const toggle = (v: string) => {
    if (props.value.includes(v)) props.onChange(props.value.filter((x) => x !== v));
    else props.onChange([...props.value, v]);
  };
  return (
    <>
      <input
        className="search" autoFocus
        placeholder="Type to search…"
        value={q} onChange={(e) => setQ(e.target.value)}
      />
      <div className="actions">
        <button type="button" onClick={() => props.onChange(filtered.map((o) => o.value))}>Select shown</button>
        <button type="button" onClick={() => props.onChange([])}>Clear</button>
        <span style={{ flex: 1 }} />
        <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>
          {filtered.length}/{props.options.length}
        </span>
      </div>
      <div className="options">
        {filtered.length === 0 && <div className="empty">No matches.</div>}
        {filtered.map((o) => (
          <label key={o.value}>
            <input
              type="checkbox" checked={props.value.includes(o.value)}
              onChange={() => toggle(o.value)}
            />
            <span style={{ flex: 1 }}>{o.label}</span>
            {o.hint && <span className="muted" style={{ fontSize: 11 }}>{o.hint}</span>}
          </label>
        ))}
      </div>
    </>
  );
}

function useDismiss(open: boolean, refs: React.RefObject<HTMLElement | null>[], close: () => void) {
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      const target = e.target as Node;
      if (refs.every((r) => r.current && !r.current.contains(target))) close();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}

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
  const wrap = useRef<HTMLDivElement>(null);

  useDismiss(open, [wrap], () => setOpen(false));

  const triggerText = value.length === 0
    ? (props.placeholder || `All ${label.toLowerCase()}`)
    : value.length === 1
      ? (options.find((o) => o.value === value[0])?.label || value[0])
      : `${value.length} selected`;

  return (
    <div className="smart-select" ref={wrap} style={{ width: props.width }}>
      <button type="button" className="trigger" onClick={() => setOpen(!open)}>
        <span className="muted">{label}:</span> {triggerText}
        {value.length > 0 && <span className="badge">{value.length}</span>}
      </button>
      {open && (
        <div className="panel">
          <OptionPanel
            options={options}
            value={value} onChange={onChange}
          />
        </div>
      )}
    </div>
  );
}

const FUNNEL_SVG = (
  <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
    <path d="M1.5 2h13l-5 6v5.2l-3 1.6V8z" fill="currentColor" />
  </svg>
);

/** Excel-style filter: a funnel icon in a table header opening the option panel. */
export function SmartFilterIcon(props: {
  label: string;
  options: SmartOption[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const { label, options, value, onChange } = props;
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useDismiss(open, [btnRef, panelRef], () => setOpen(false));

  const openPanel = () => {
    const rect = btnRef.current?.getBoundingClientRect();
    if (rect) {
      const panelWidth = 280;
      setPos({
        top: rect.bottom + 4,
        left: Math.max(4, Math.min(rect.left, window.innerWidth - panelWidth - 8)),
      });
    }
    setOpen(true);
  };

  return (
    <>
      <button
        type="button"
        ref={btnRef}
        className={"column-filter-btn" + (value.length > 0 ? " has-value" : "")}
        title={`Filter by ${label}`}
        aria-label={`Filter by ${label}`}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          open ? setOpen(false) : openPanel();
        }}
      >
        {FUNNEL_SVG}
        {value.length > 0 && <span className="column-filter-count">{value.length}</span>}
      </button>
      {open && pos && (
        <div
          className="smart-select column-filter-pop"
          ref={panelRef}
          style={{ position: "fixed", top: pos.top, left: pos.left, zIndex: 60 }}
        >
          <div className="panel" style={{ margin: 0, width: 280, maxWidth: "none", maxHeight: 320 }}>
            <OptionPanel
              options={options}
              value={value} onChange={onChange}
            />
          </div>
        </div>
      )}
    </>
  );
}
