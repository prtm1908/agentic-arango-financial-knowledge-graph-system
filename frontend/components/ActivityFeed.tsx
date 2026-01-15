"use client";

import { useMemo } from "react";

type ToolCall = {
  id: string;
  tool: string;
  server: string;
  args: Record<string, any>;
  result?: any;
  duration_ms?: number;
  timestamp: string;
};

type AgentActivity = {
  agent: string;
  reason: string;
  tools: ToolCall[];
  timestamp: string;
};

type EventItem = {
  id: string;
  type: string;
  payload: Record<string, any>;
  timestamp: string;
};

type ActivityFeedProps = {
  events: EventItem[];
  status: string;
};

function groupEventsByAgent(events: EventItem[]): AgentActivity[] {
  const agents: AgentActivity[] = [];
  const agentsByName = new Map<string, AgentActivity>();
  let currentAgent: AgentActivity | null = null;

  const getOrCreateAgent = (name: string, reason = "", timestamp = new Date().toISOString()) => {
    const normalized = name || "Unknown Agent";
    if (!agentsByName.has(normalized)) {
      const section: AgentActivity = {
        agent: normalized,
        reason,
        tools: [],
        timestamp
      };
      agentsByName.set(normalized, section);
      agents.push(section);
    }
    return agentsByName.get(normalized)!;
  };

  // Process events in chronological order (oldest first)
  const chronological = [...events].reverse();

  for (const event of chronological) {
    if (event.type === "agent_switch") {
      // Start a new agent section
      currentAgent = getOrCreateAgent(
        event.payload.agent || "Unknown Agent",
        event.payload.reason || "",
        event.timestamp
      );
    } else if (event.type === "tool_call" && currentAgent) {
      // Add tool to current agent
      const agentName = event.payload.agent || currentAgent.agent;
      const targetAgent = getOrCreateAgent(agentName, currentAgent.reason, currentAgent.timestamp);
      targetAgent.tools.push({
        id: event.id,
        tool: event.payload.tool || "unknown",
        server: event.payload.server || "",
        args: event.payload.args || {},
        timestamp: event.timestamp
      });
    } else if (event.type === "tool_call") {
      const targetAgent = getOrCreateAgent(
        event.payload.agent || "Router",
        "",
        event.timestamp
      );
      targetAgent.tools.push({
        id: event.id,
        tool: event.payload.tool || "unknown",
        server: event.payload.server || "",
        args: event.payload.args || {},
        timestamp: event.timestamp
      });
    } else if (event.type === "tool_result") {
      // Update the matching tool with its result
      const targetAgent = getOrCreateAgent(
        event.payload.agent || currentAgent?.agent || "Router",
        currentAgent?.reason || "",
        event.timestamp
      );
      const tool = targetAgent.tools.find(
        (t) => t.tool === event.payload.tool && !t.result
      );
      if (tool) {
        tool.result = event.payload.result;
        tool.duration_ms = event.payload.duration_ms;
      }
    } else if (event.type === "aql_query" && currentAgent) {
      // Add AQL query as a tool call
      currentAgent.tools.push({
        id: event.id,
        tool: "AQL Query",
        server: "arangodb",
        args: { query: event.payload.query, bind_vars: event.payload.bind_vars },
        timestamp: event.timestamp
      });
    }
  }

  // If no agent switches but we have tool calls, create a default agent
  if (agents.length === 0 && events.some((e) => e.type === "tool_call" || e.type === "aql_query")) {
    const defaultAgent: AgentActivity = {
      agent: "Router",
      reason: "Processing request",
      tools: [],
      timestamp: new Date().toISOString()
    };

    for (const event of chronological) {
      if (event.type === "tool_call") {
        defaultAgent.tools.push({
          id: event.id,
          tool: event.payload.tool || "unknown",
          server: event.payload.server || "",
          args: event.payload.args || {},
          timestamp: event.timestamp
        });
      } else if (event.type === "aql_query") {
        defaultAgent.tools.push({
          id: event.id,
          tool: "AQL Query",
          server: "arangodb",
          args: { query: event.payload.query },
          timestamp: event.timestamp
        });
      }
    }

    if (defaultAgent.tools.length > 0) {
      agents.push(defaultAgent);
    }
  }

  return agents;
}

function formatAgentName(name: string): string {
  return name
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatToolArgs(args: Record<string, any>): string {
  const query = typeof args.query === "string" ? args.query.trim() : "";
  const aqlQuery = typeof args.aql_query === "string" ? args.aql_query.trim() : "";
  const normalizedQuery = query || aqlQuery;
  const extraKeys = Object.keys(args).filter(
    (key) => !["query", "aql_query"].includes(key) && args[key] !== undefined
  );

  if (normalizedQuery && extraKeys.length === 0) {
    return normalizedQuery;
  }

  if (Object.keys(args).length > 0) {
    return JSON.stringify(args, null, 2);
  }

  return "";
}

export default function ActivityFeed({ events, status }: ActivityFeedProps) {
  const agents = useMemo(() => groupEventsByAgent(events), [events]);
  const isComplete = events.some((e) => e.type === "complete");
  const hasError = events.some((e) => e.type === "error");
  const isThinking = events.some((e) => e.type === "step_start") && agents.length === 0;

  return (
    <section className="panel" style={{ animationDelay: "0.18s" }}>
      <div className="panel-header">
        <h2>Live Activity</h2>
        <span className={`status-pill ${isComplete ? "complete" : hasError ? "error" : isThinking ? "thinking" : ""}`}>
          {isComplete ? "Complete" : hasError ? "Error" : status || "Idle"}
        </span>
      </div>
      <div className="activity-list">
        {agents.length === 0 && !isThinking && (
          <div className="activity-empty">
            Submit a query to see agent activity and tool calls.
          </div>
        )}
        {isThinking && agents.length === 0 && (
          <div className="thinking-indicator">
            <div className="thinking-dots">
              <span></span><span></span><span></span>
            </div>
            <span>Router is analyzing your request...</span>
          </div>
        )}
        {agents.map((agent, idx) => (
          <details key={`${agent.agent}-${idx}`} className="agent-section" open>
            <summary className="agent-header">
              <span className="agent-name">{formatAgentName(agent.agent)}</span>
              {agent.reason && <span className="agent-reason">{agent.reason}</span>}
              <span className="tool-count">{agent.tools.length} tool{agent.tools.length !== 1 ? "s" : ""}</span>
            </summary>
            <div className="tool-list">
              {agent.tools.length === 0 && (
                <div className="tool-item empty">No tools called yet...</div>
              )}
              {agent.tools.map((tool) => (
                <details key={tool.id} className="tool-item">
                  <summary className="tool-header">
                    <span className="tool-name">{tool.tool}</span>
                    {tool.duration_ms !== undefined && (
                      <span className="tool-duration">{tool.duration_ms}ms</span>
                    )}
                    <span className="tool-time">
                      {new Date(tool.timestamp).toLocaleTimeString()}
                    </span>
                  </summary>
                  <div className="tool-details">
                    {formatToolArgs(tool.args) && (
                      <div className="tool-section">
                        <div className="tool-section-label">Input</div>
                        <pre className="tool-code">{formatToolArgs(tool.args)}</pre>
                      </div>
                    )}
                    {tool.result !== undefined && (
                      <div className="tool-section">
                        <div className="tool-section-label">Output</div>
                        <pre className="tool-code">
                          {typeof tool.result === "string"
                            ? tool.result
                            : JSON.stringify(tool.result, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                </details>
              ))}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
