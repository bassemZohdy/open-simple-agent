import { useRef } from "react";

interface SearchFieldProps {
  id: string;
  label: string;
  value: string;
  placeholder?: string;
  clearLabel: string;
  onChange: (value: string) => void;
}

/** Shared submit-based search field with an explicit, keyboard-accessible clear action. */
export function SearchField({ id, label, value, placeholder, clearLabel, onChange }: SearchFieldProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  function clear() {
    onChange("");
    inputRef.current?.focus();
  }

  return (
    <div className="search-field">
      <label htmlFor={id}>{label}</label>
      <span className="search-input-row">
        <input ref={inputRef} id={id} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
        {value ? (
          <button type="button" className="clear-search-button" aria-label={clearLabel} onClick={clear}>
            ×
          </button>
        ) : null}
      </span>
    </div>
  );
}
