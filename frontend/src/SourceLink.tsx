import { Link } from "react-router-dom";
import { SourceRef } from "./api";

export function SourceLink({ source, title }: { source: SourceRef; title: string }) {
  const params = new URLSearchParams({
    statement: String(source.statement_id),
    ref: `${source.kind}:${source.id}`,
  });
  return (
    <Link
      className="source-row-link"
      to={`/verify?${params.toString()}`}
      title={title}
      aria-label={title}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 3h9l3 3v15H6z" />
        <path d="M14 3v4h4M9 11h6M9 15h6" />
      </svg>
    </Link>
  );
}
