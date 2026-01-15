"use client";

import { useMemo, useState } from "react";

type EventItem = {
  type: string;
  payload: Record<string, unknown>;
};

type ToolCall = {
  id: string;
  tool: string;
  server: string;
  args: Record<string, unknown>;
  result?: unknown;
  duration_ms?: number;
};

type AgentSection = {
  agent: string;
  reason: string;
  tools: ToolCall[];
};

type AgentActivityProps = {
  events: EventItem[];
  isStreaming?: boolean;
};

function formatToolArgs(args: Record<string, unknown>): string {
  const query = typeof args.query === "string" ? args.query.trim() : "";
  const aqlQuery = typeof args.aql_query === "string" ? args.aql_query.trim() : "";
  const normalizedQuery = query || aqlQuery;
  const extraKeys = Object.keys(args).filter(
    key => !["query", "aql_query"].includes(key) && args[key] !== undefined
  );

  if (normalizedQuery && extraKeys.length === 0) {
    return normalizedQuery;
  }

  if (Object.keys(args).length > 0) {
    return JSON.stringify(args, null, 2);
  }

  return "";
}

function groupEventsByAgent(events: EventItem[]): AgentSection[] {
  const agents: AgentSection[] = [];
  const agentsByName = new Map<string, AgentSection>();
  let currentAgent: AgentSection | null = null;

  const getOrCreateAgent = (name: string, reason = "") => {
    const normalized = name || "Unknown";
    if (!agentsByName.has(normalized)) {
      const section: AgentSection = { agent: normalized, reason, tools: [] };
      agentsByName.set(normalized, section);
      agents.push(section);
    }
    return agentsByName.get(normalized)!;
  };

  for (const event of events) {
    if (event.type === "agent_switch") {
      currentAgent = getOrCreateAgent(
        (event.payload.agent as string) || "Unknown",
        (event.payload.reason as string) || ""
      );
    } else if (event.type === "tool_call" && currentAgent) {
      const toolName = (event.payload.tool as string) || "unknown";
      const agentName = (event.payload.agent as string) || currentAgent.agent;
      const targetAgent = getOrCreateAgent(agentName, currentAgent.reason);
      const toolIndex = targetAgent.tools.length;
      const toolId =
        (event.payload.call_id as string) ||
        (event.payload.callID as string) ||
        `${targetAgent.agent}-${toolIndex}-${toolName}`;
      targetAgent.tools.push({
        id: toolId,
        tool: toolName,
        server: (event.payload.server as string) || "",
        args: (event.payload.args as Record<string, unknown>) || {}
      });
    } else if (event.type === "tool_call") {
      const toolName = (event.payload.tool as string) || "unknown";
      const agentName = (event.payload.agent as string) || "Router";
      const targetAgent = getOrCreateAgent(agentName);
      const toolIndex = targetAgent.tools.length;
      const toolId =
        (event.payload.call_id as string) ||
        (event.payload.callID as string) ||
        `${targetAgent.agent}-${toolIndex}-${toolName}`;
      targetAgent.tools.push({
        id: toolId,
        tool: toolName,
        server: (event.payload.server as string) || "",
        args: (event.payload.args as Record<string, unknown>) || {}
      });
    } else if (event.type === "tool_result") {
      const agentName = (event.payload.agent as string) || currentAgent?.agent || "Router";
      const targetAgent = getOrCreateAgent(agentName, currentAgent?.reason || "");
      const tool = targetAgent.tools.find(
        t => t.tool === event.payload.tool && !t.result
      );
      if (tool) {
        tool.result = event.payload.result;
        tool.duration_ms = event.payload.duration_ms as number;
      }
    } else if (event.type === "aql_query" && currentAgent) {
      const toolIndex = currentAgent.tools.length;
      const toolId = `${currentAgent.agent}-${toolIndex}-aql`;
      currentAgent.tools.push({
        id: toolId,
        tool: "AQL Query",
        server: "arangodb",
        args: {
          query: event.payload.query,
          bind_vars: event.payload.bind_vars
        }
      });
    }
  }

  // If no agent switches but we have tool calls, create a default agent
  if (agents.length === 0) {
    const toolCalls = events.filter(e => e.type === "tool_call" || e.type === "aql_query");
    if (toolCalls.length > 0) {
      const defaultAgent: AgentSection = {
        agent: "Router",
        reason: "Processing",
        tools: []
      };
      for (const event of events) {
        if (event.type === "tool_call") {
          const toolIndex = defaultAgent.tools.length;
          const toolName = (event.payload.tool as string) || "unknown";
          const toolId =
            (event.payload.call_id as string) ||
            (event.payload.callID as string) ||
            `${defaultAgent.agent}-${toolIndex}-${toolName}`;
          defaultAgent.tools.push({
            id: toolId,
            tool: toolName,
            server: (event.payload.server as string) || "",
            args: (event.payload.args as Record<string, unknown>) || {}
          });
        } else if (event.type === "aql_query") {
          const toolIndex = defaultAgent.tools.length;
          const toolId = `${defaultAgent.agent}-${toolIndex}-aql`;
          defaultAgent.tools.push({
            id: toolId,
            tool: "AQL Query",
            server: "arangodb",
            args: { query: event.payload.query }
          });
        }
      }
      agents.push(defaultAgent);
    }
  }

  return agents;
}

function formatAgentName(name: string): string {
  return name
    .replace(/-/g, " ")
    .replace(/_/g, " ")
    .replace(/\b\w/g, c => c.toUpperCase());
}

export default function AgentActivity({ events, isStreaming }: AgentActivityProps) {
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});
  const agents = useMemo(() => groupEventsByAgent(events), [events]);

  if (agents.length === 0 && !isStreaming) {
    return null;
  }

  const toggleTool = (toolId: string) => {
    setExpandedTools(prev => ({ ...prev, [toolId]: !prev[toolId] }));
  };

  return (
    <div className="agent-activity">
      {isStreaming && agents.length === 0 && (
        <div className="activity-streaming">
          <div className="streaming-dots">
            <span></span><span></span><span></span>
          </div>
          <span>Processing...</span>
        </div>
      )}
      {agents.map((agent, idx) => (
        <details key={`${agent.agent}-${idx}`} className="activity-agent" open>
          <summary className="activity-agent-header">
            <span className="activity-agent-name">{formatAgentName(agent.agent)}</span>
            {agent.reason && <span className="activity-agent-reason">{agent.reason}</span>}
            <span className="activity-tool-count">
              {agent.tools.length} tool{agent.tools.length !== 1 ? "s" : ""}
            </span>
          </summary>
          <div className="activity-tools">
            {agent.tools.map(tool => {
              const inputText = formatToolArgs(tool.args);
              const hasOutput = tool.result !== undefined;

              return (
                <div key={tool.id} className="activity-tool">
                  <button
                    className="activity-tool-header"
                    onClick={() => toggleTool(tool.id)}
                  >
                    <span className="activity-tool-name">{tool.tool}</span>
                    {tool.duration_ms && (
                      <span className="activity-tool-duration">{tool.duration_ms}ms</span>
                    )}
                  </button>
                  {expandedTools[tool.id] && (
                    <div className="activity-tool-details">
                      {inputText && (
                        <div className="tool-detail-section">
                          <div className="tool-detail-label">Input</div>
                          <pre className="tool-detail-code">{inputText}</pre>
                        </div>
                      )}
                      {hasOutput && (
                        <div className="tool-detail-section">
                          <div className="tool-detail-label">Output</div>
                          <pre className="tool-detail-code">
                            {typeof tool.result === "string"
                              ? tool.result
                              : JSON.stringify(tool.result, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      ))}
    </div>
  );
}
