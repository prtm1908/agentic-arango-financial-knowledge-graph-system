"use client";

import { useState, useRef, useEffect } from "react";

type ChatInputProps = {
  onSubmit: (message: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

const EXAMPLE_QUERIES = [
  "What subsidiaries does Reliance have?",
  "Extract PAT from TCS FY24 annual report",
  "Show me the revenue for Infosys FY24",
  "Export TCS metrics to Excel"
];

export default function ChatInput({ onSubmit, disabled, placeholder }: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 150)}px`;
    }
  }, [message]);

  // Close settings on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowSettings(false);
      }
    };
    if (showSettings) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showSettings]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (message.trim() && !disabled) {
      onSubmit(message.trim());
      setMessage("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const handleExampleClick = (example: string) => {
    setMessage(example);
    textareaRef.current?.focus();
  };

  return (
    <div className="chat-input-container">
      <div className="example-queries">
        {EXAMPLE_QUERIES.map((example, idx) => (
          <button
            key={idx}
            className="example-chip"
            onClick={() => handleExampleClick(example)}
            disabled={disabled}
          >
            {example}
          </button>
        ))}
      </div>

      <form className="chat-input-form" onSubmit={handleSubmit}>
        <div className="chat-input-wrapper">
          <textarea
            ref={textareaRef}
            className="chat-input"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || "Ask about financial data, extract metrics, or generate reports..."}
            disabled={disabled}
            rows={1}
          />

          <div className="chat-input-actions">
            <div className="settings-wrapper" ref={settingsRef}>
              <button
                type="button"
                className="settings-button"
                onClick={() => setShowSettings(!showSettings)}
                title="Settings"
              >
                ⚙
              </button>
              {showSettings && (
                <div className="settings-popover">
                  <div className="settings-title">Settings</div>
                  <div className="settings-note">
                    Chat settings coming soon. Configure model, verbosity, and more.
                  </div>
                </div>
              )}
            </div>

            <button
              type="submit"
              className="send-button"
              disabled={disabled || !message.trim()}
              title="Send message"
            >
              {disabled ? (
                <span className="send-loading"></span>
              ) : (
                "→"
              )}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
