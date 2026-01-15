"use client";

import { useEffect, useState } from "react";
import ChatListItem from "./ChatListItem";

type Chat = {
  chat_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview?: string;
};

type ChatListProps = {
  currentChatId: string | null;
  onSelectChat: (chatId: string) => void;
  refreshTrigger?: number;
};

export default function ChatList({ currentChatId, onSelectChat, refreshTrigger }: ChatListProps) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchChats = async () => {
    try {
      setLoading(true);
      const response = await fetch("/api/chats?limit=50");
      if (!response.ok) {
        throw new Error("Failed to fetch chats");
      }
      const data = await response.json();
      setChats(data.chats || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load chats");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchChats();
  }, [refreshTrigger]);

  const handleDelete = async (chatId: string) => {
    try {
      const response = await fetch(`/api/chats/${chatId}`, {
        method: "DELETE"
      });
      if (response.ok) {
        setChats(prev => prev.filter(c => c.chat_id !== chatId));
        if (currentChatId === chatId) {
          onSelectChat("");
        }
      }
    } catch (err) {
      console.error("Failed to delete chat:", err);
    }
  };

  if (loading) {
    return (
      <div className="chat-list">
        <div className="sidebar-section-title">Recent Chats</div>
        <div className="chat-list-loading">
          <div className="loading-skeleton"></div>
          <div className="loading-skeleton"></div>
          <div className="loading-skeleton"></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="chat-list">
        <div className="sidebar-section-title">Recent Chats</div>
        <div className="chat-list-error">{error}</div>
      </div>
    );
  }

  return (
    <div className="chat-list">
      <div className="sidebar-section-title">Recent Chats</div>
      {chats.length === 0 ? (
        <div className="chat-list-empty">No chats yet. Start a new conversation!</div>
      ) : (
        <div className="chat-list-items">
          {chats.map(chat => (
            <ChatListItem
              key={chat.chat_id}
              chat={chat}
              isActive={chat.chat_id === currentChatId}
              onSelect={() => onSelectChat(chat.chat_id)}
              onDelete={() => handleDelete(chat.chat_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
