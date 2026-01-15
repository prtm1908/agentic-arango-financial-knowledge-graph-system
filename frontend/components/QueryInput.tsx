"use client";

import { useState, type FormEvent } from "react";

const EXAMPLES = [
  "What subsidiaries does Reliance have?",
  "Extract PAT from TCS FY24 annual report",
  "Show me the revenue for Infosys FY24",
  "Export TCS metrics to Excel"
];

type QueryInputProps = {
  onSubmit: (query: string) => Promise<void> | void;
  disabled?: boolean;
};

export default function QueryInput({ onSubmit, disabled }: QueryInputProps) {
  const [query, setQuery] = useState("");

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }
    await onSubmit(trimmed);
  };

  return (
    <div className="query-panel">
      <form className="query-form" onSubmit={handleSubmit}>
        <textarea
          className="query-textarea"
          placeholder="Ask about companies, filings, or extract a metric..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={disabled}
        />
        <div className="query-actions">
          <div className="example-row">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                className="chip"
                onClick={() => setQuery(example)}
                disabled={disabled}
              >
                {example}
              </button>
            ))}
          </div>
          <button className="query-button" type="submit" disabled={disabled}>
            {disabled ? "Working..." : "Run query"}
          </button>
        </div>
      </form>
    </div>
  );
}
