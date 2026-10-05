import { useEffect, useRef, useState } from "react";
import { parseNumber } from "../lib/format";

interface Props {
  value: number | string | null;
  kind: "int" | "number" | "text";
  label: string;
  disabled?: boolean;
  format?: (v: number | string) => string;
  onSave: (v: number | string | null) => Promise<boolean>;
}

/** Click to edit; Enter or blur saves, Escape cancels. Empty clears the value. */
export function EditableCell({ value, kind, label, disabled, format, onSave }: Props) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    if (editing) {
      input.current?.focus();
      input.current?.select();
    }
  }, [editing]);

  const start = () => {
    done.current = false;
    setText(value == null ? "" : String(value));
    setErr(null);
    setEditing(true);
  };

  const commit = async () => {
    if (done.current) return;
    let v: number | string | null;
    if (kind === "text") v = text.trim() === "" ? null : text;
    else if (text.trim() === "") v = null;
    else {
      const n = parseNumber(text);
      if (n == null || (kind === "int" && !Number.isInteger(n))) {
        setErr(kind === "int" ? "Whole number" : "Number");
        return;
      }
      v = n;
    }
    done.current = true;
    if (v === value || (v == null && value == null)) {
      setEditing(false);
      return;
    }
    setSaving(true);
    const ok = await onSave(v);
    setSaving(false);
    if (ok) setEditing(false);
    else done.current = false;
  };

  if (editing) {
    return (
      <span className="edit-cell">
        <input
          ref={input}
          className={`input input-sm ${kind === "text" ? "input-text" : ""}`}
          aria-label={label}
          value={text}
          disabled={saving}
          inputMode={kind === "text" ? undefined : "decimal"}
          aria-invalid={err ? true : undefined}
          onChange={(e) => {
            setText(e.target.value);
            setErr(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void commit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              done.current = true;
              setEditing(false);
            }
          }}
          onBlur={() => void commit()}
        />
        {err && <span className="field-error small">{err}</span>}
      </span>
    );
  }
  const shown = value == null || value === "" ? "—" : format ? format(value) : String(value);
  return (
    <button type="button" className={`cell-btn ${value == null ? "muted" : ""}`} aria-label={`Edit ${label}`} disabled={disabled} onClick={start}>
      {shown}
    </button>
  );
}
