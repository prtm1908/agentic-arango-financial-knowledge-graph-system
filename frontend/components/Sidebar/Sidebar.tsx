"use client";

import ChatList from "./ChatList";
import AgentsDropdown from "./AgentsDropdown";

type SidebarProps = {
  isOpen: boolean;
  onClose: () => void;
  currentChatId: string | null;
  onSelectChat: (chatId: string) => void;
  onNewChat: () => void;
  refreshTrigger?: number;
};

export default function Sidebar({
  isOpen,
  onClose,
  currentChatId,
  onSelectChat,
  onNewChat,
  refreshTrigger
}: SidebarProps) {
  return (
    <>
      {/* Overlay for mobile */}
      {isOpen && <div className="sidebar-overlay" onClick={onClose} />}

      <aside className={`sidebar ${isOpen ? "open" : ""}`}>
        <div className="sidebar-header">
          <h2 className="sidebar-title">Financial KG</h2>
          <button className="sidebar-close" onClick={onClose} title="Close sidebar">
            ×
          </button>
        </div>

        <button className="new-chat-button" onClick={onNewChat}>
          <span className="new-chat-icon">+</span>
          <span>New Chat</span>
        </button>

        <div className="sidebar-content">
          <ChatList
            currentChatId={currentChatId}
            onSelectChat={onSelectChat}
            refreshTrigger={refreshTrigger}
          />

          <div className="sidebar-divider" />

          <AgentsDropdown />
        </div>
      </aside>
    </>
  );
}
