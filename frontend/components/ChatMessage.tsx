"use client";

import MarkdownRenderer from "./MarkdownRenderer";

type Message = {
  id: string;
  role: "user" | "system";
  content: string;
  timestamp: string;
};

type ChatMessageProps = {
  message: Message;
};

export default function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const formattedTime = new Date(message.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit"
  });

  return (
    <div className={`chat-message ${isUser ? "user" : "system"}`}>
      <div className="message-header">
        <span className="message-role">{isUser ? "You" : "Assistant"}</span>
        <span className="message-time">{formattedTime}</span>
      </div>

      <div className="message-content">
        {isUser ? (
          <p>{message.content}</p>
        ) : (
          <MarkdownRenderer content={message.content} />
        )}
      </div>
    </div>
  );
}
