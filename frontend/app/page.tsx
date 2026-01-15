"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import Sidebar from "../components/Sidebar/Sidebar";
import ChatWindow from "../components/ChatWindow";
import ChatInput from "../components/ChatInput";

type ToolCallInfo = {
  tool: string;
  server: string;
  args?: Record<string, unknown>;
  duration_ms?: number;
  agent?: string;
};

type MessageMetadata = {
  agents_used?: string[];
  tools_called?: ToolCallInfo[];
  event_history?: Record<string, unknown>[];
  job_id?: string;
};

type Message = {
  id: string;
  role: "user" | "system";
  content: string;
  timestamp: string;
  metadata?: MessageMetadata;
};

type EventItem = {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  timestamp: string;
};

const EVENT_TYPES = [
  "connected",
  "agent_switch",
  "tool_call",
  "tool_result",
  "metric_found",
  "aql_query",
  "status",
  "complete",
  "error",
  "step_start"
];

export default function HomePage() {
  // Sidebar state
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Chat state
  const [currentChatId, setCurrentChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [chatRefreshTrigger, setChatRefreshTrigger] = useState(0);

  // Streaming state
  const [events, setEvents] = useState<EventItem[]>([]);
  const [streamingContent, setStreamingContent] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);

  // Cleanup event source on unmount
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  // Load chat when currentChatId changes
  useEffect(() => {
    if (currentChatId) {
      loadChat(currentChatId);
    } else {
      setMessages([]);
    }
  }, [currentChatId]);

  const loadChat = async (chatId: string) => {
    try {
      const response = await fetch(`/api/chats/${chatId}`);
      if (!response.ok) {
        throw new Error("Failed to load chat");
      }
      const data = await response.json();
      setMessages(data.messages || []);
    } catch (err) {
      console.error("Failed to load chat:", err);
      setError(err instanceof Error ? err.message : "Failed to load chat");
    }
  };

  const createNewChat = async () => {
    setCurrentChatId(null);
    setMessages([]);
    setEvents([]);
    setStreamingContent("");
    setStatus("");
    setError(null);
  };

  const selectChat = (chatId: string) => {
    if (chatId === currentChatId) return;
    setCurrentChatId(chatId || null);
    setEvents([]);
    setStreamingContent("");
    setStatus("");
    setError(null);
  };

  const startEventStream = useCallback((jobId: string, chatId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const eventSource = new EventSource(`/api/events?jobId=${encodeURIComponent(jobId)}`);
    eventSourceRef.current = eventSource;

    const handleEvent = (event: MessageEvent) => {
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(event.data);
      } catch {
        payload = { message: event.data };
      }

      const eventType = (payload.type as string) || event.type || "message";
      const item: EventItem = {
        id: `${eventType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        type: eventType,
        payload,
        timestamp: new Date().toISOString()
      };

      setEvents(prev => [...prev, item]);

      if (eventType === "connected") {
        setStatus("Connected, waiting for worker...");
        return;
      }

      if (eventType === "status") {
        setStatus((payload.message as string) || "");
      }

      if (eventType === "step_start") {
        setStatus("Thinking...");
      }

      if (eventType === "complete") {
        const result = payload.result ?? payload;
        let responseText = "";

        if (typeof result === "object" && result !== null) {
          const r = result as Record<string, unknown>;
          responseText = (r.response as string) || (r.text as string) || JSON.stringify(result, null, 2);
        } else {
          responseText = String(result);
        }

        setStreamingContent(responseText);
        setIsLoading(false);
        eventSource.close();

        // Refresh chat list and reload chat to get the saved message
        setChatRefreshTrigger(prev => prev + 1);
        setTimeout(() => loadChat(chatId), 500);
      }

      if (eventType === "error") {
        setError((payload.message as string) || "An error occurred");
        setIsLoading(false);
        eventSource.close();
      }
    };

    EVENT_TYPES.forEach(eventType => {
      eventSource.addEventListener(eventType, handleEvent as EventListener);
    });

    eventSource.onerror = () => {
      setError("Event stream interrupted. Try again.");
      setIsLoading(false);
      eventSource.close();
    };
  }, []);

  const handleSubmit = async (query: string) => {
    setEvents([]);
    setStreamingContent("");
    setStatus("Submitting query...");
    setError(null);
    setIsLoading(true);

    try {
      let chatId = currentChatId;

      // Create new chat if none exists
      if (!chatId) {
        const createResponse = await fetch("/api/chats", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: query.slice(0, 50) })
        });

        if (!createResponse.ok) {
          throw new Error("Failed to create chat");
        }

        const chatData = await createResponse.json();
        chatId = chatData.chat_id;
        setCurrentChatId(chatId);
        setChatRefreshTrigger(prev => prev + 1);
      }

      // Add user message to local state immediately (query endpoint will persist it)
      const userMessage: Message = {
        id: `user-${Date.now()}`,
        role: "user",
        content: query,
        timestamp: new Date().toISOString()
      };
      setMessages(prev => [...prev, userMessage]);

      // Submit query with chat context
      const response = await fetch(`/api/chats/${chatId}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query })
      });

      if (!response.ok) {
        const message = await response.text();
        throw new Error(message || "Failed to submit query");
      }

      const data = await response.json();
      const jobId = data.job_id as string;

      // Start event stream with chatId for reloading after completion
      startEventStream(jobId, chatId as string);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setError(message);
      setIsLoading(false);
    }
  };

  // Convert events for AgentActivity component
  const streamingEvents = events.map(e => ({
    type: e.type,
    payload: e.payload
  }));

  return (
    <div className="app-layout">
      {/* Mobile header with hamburger */}
      <header className="mobile-header">
        <button
          className="hamburger-button"
          onClick={() => setSidebarOpen(!sidebarOpen)}
          aria-label="Toggle sidebar"
        >
          ☰
        </button>
        <h1 className="mobile-title">Financial KG</h1>
      </header>

      <Sidebar
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        currentChatId={currentChatId}
        onSelectChat={selectChat}
        onNewChat={createNewChat}
        refreshTrigger={chatRefreshTrigger}
      />

      <main className="main-content">
        <div className="chat-container">
          {error && (
            <div className="error-banner">
              <span>{error}</span>
              <button onClick={() => setError(null)}>×</button>
            </div>
          )}

          <ChatWindow
            messages={messages}
            isLoading={isLoading}
            streamingEvents={streamingEvents}
            streamingContent={streamingContent}
          />

          <ChatInput
            onSubmit={handleSubmit}
            disabled={isLoading}
            placeholder={status || undefined}
          />
        </div>
      </main>
    </div>
  );
}
