"use client";

import { Fragment, useEffect, useRef } from "react";
import ChatMessage from "./ChatMessage";
import AgentActivity from "./AgentActivity";

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
  type: string;
  payload: Record<string, unknown>;
};

type ChatWindowProps = {
  messages: Message[];
  isLoading?: boolean;
  streamingEvents?: EventItem[];
  streamingContent?: string;
};

function convertMetadataToEvents(metadata: MessageMetadata): EventItem[] {
  if (metadata.event_history && metadata.event_history.length > 0) {
    return metadata.event_history.map(event => ({
      type: (event.type as string) || "message",
      payload: event
    }));
  }

  const events: EventItem[] = [];

  const tools = metadata.tools_called || [];
  const hasToolAgents = tools.some(tool => !!tool.agent);
  const agentsFromTools = new Set<string>();

  if (tools.length > 0 && hasToolAgents) {
    let currentAgent = "";
    for (const tool of tools) {
      const agentName = tool.agent || currentAgent || "Unknown";
      if (agentName && agentName !== currentAgent) {
        events.push({
          type: "agent_switch",
          payload: { agent: agentName, reason: "" }
        });
        currentAgent = agentName;
      }
      if (tool.agent) {
        agentsFromTools.add(tool.agent);
      }
      events.push({
        type: "tool_call",
        payload: {
          tool: tool.tool,
          server: tool.server,
          args: tool.args || {},
          agent: tool.agent
        }
      });
    }
  } else {
    if (metadata.agents_used) {
      for (const agent of metadata.agents_used) {
        events.push({
          type: "agent_switch",
          payload: { agent, reason: "" }
        });
      }
    }

    if (tools.length > 0) {
      for (const tool of tools) {
        events.push({
          type: "tool_call",
          payload: {
            tool: tool.tool,
            server: tool.server,
            args: tool.args || {},
            agent: tool.agent
          }
        });
      }
    }
    return events;
  }

  if (metadata.agents_used) {
    for (const agent of metadata.agents_used) {
      if (!agentsFromTools.has(agent)) {
        events.push({
          type: "agent_switch",
          payload: { agent, reason: "" }
        });
      }
    }
  }

  return events;
}

export default function ChatWindow({
  messages,
  isLoading,
  streamingEvents,
  streamingContent
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent, streamingEvents]);

  return (
    <div className="chat-window">
      {messages.length === 0 && !isLoading && (
        <div className="chat-empty">
          <div className="chat-empty-icon">💬</div>
          <h3>Start a Conversation</h3>
          <p>
            Ask questions about companies, extract financial metrics from reports,
            or generate Excel exports. Try one of the example queries below.
          </p>
        </div>
      )}

      {messages.map(message => {
        const activityEvents =
          message.role === "system" && message.metadata
            ? convertMetadataToEvents(message.metadata)
            : [];

        return (
          <Fragment key={message.id}>
            {activityEvents.length > 0 && (
              <div className="chat-activity">
                <div className="chat-activity-header">Agent activity</div>
                <AgentActivity events={activityEvents} isStreaming={false} />
              </div>
            )}
            <ChatMessage message={message} />
          </Fragment>
        );
      })}

      {/* Streaming message placeholder */}
      {isLoading && (
        <>
          {streamingEvents && streamingEvents.length > 0 && (
            <div className="chat-activity">
              <div className="chat-activity-header">Agent activity</div>
              <AgentActivity events={streamingEvents} isStreaming={true} />
            </div>
          )}
          <ChatMessage
            message={{
              id: "streaming",
              role: "system",
              content: streamingContent || "Thinking...",
              timestamp: new Date().toISOString()
            }}
          />
        </>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
