"use client";

import { useState } from "react";

type Chat = {
  chat_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview?: string;
};

type ChatListItemProps = {
  chat: Chat;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
};

export default function ChatListItem({ chat, isActive, onSelect, onDelete }: ChatListItemProps) {
  const [showDelete, setShowDelete] = useState(false);

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } else if (diffDays === 1) {
      return "Yesterday";
    } else if (diffDays < 7) {
      return date.toLocaleDateString([], { weekday: "short" });
    } else {
      return date.toLocaleDateString([], { month: "short", day: "numeric" });
    }
  };

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm("Delete this chat?")) {
      onDelete();
    }
  };

  return (
    <div
      className={`chat-list-item ${isActive ? "active" : ""}`}
      onClick={onSelect}
      onMouseEnter={() => setShowDelete(true)}
      onMouseLeave={() => setShowDelete(false)}
    >
      <div className="chat-item-content">
        <div className="chat-item-title">{chat.title}</div>
        <div className="chat-item-meta">
          <span className="chat-item-date">{formatDate(chat.updated_at)}</span>
          <span className="chat-item-count">{chat.message_count} msg{chat.message_count !== 1 ? "s" : ""}</span>
        </div>
      </div>
      {showDelete && (
        <button
          className="chat-item-delete"
          onClick={handleDeleteClick}
          title="Delete chat"
        >
          ×
        </button>
      )}
    </div>
  );
}
