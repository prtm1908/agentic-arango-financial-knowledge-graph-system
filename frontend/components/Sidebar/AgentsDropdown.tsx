"use client";

import { useState } from "react";

type Agent = {
  name: string;
  description: string;
  tools: string[];
};

const AGENTS: Record<string, Agent> = {
  router: {
    name: "Router",
    description: "Orchestrates and routes queries to specialized agents",
    tools: []
  },
  knowledge_graph: {
    name: "Knowledge Graph",
    description: "Queries the ArangoDB graph database",
    tools: ["fetch_schemas", "read_documents_with_filter", "arango_query", "arango_insert"]
  },
  metric_extractor: {
    name: "Metric Extractor",
    description: "Extracts financial metrics from PDF documents",
    tools: ["extract_metric"]
  },
  citation: {
    name: "Citation",
    description: "Generates visual citations with highlighted bounding boxes",
    tools: ["analyze_page_with_di", "find_value_coordinates", "render_citation_image", "generate_citation"]
  },
  exporter: {
    name: "Exporter",
    description: "Creates Excel exports and reports",
    tools: ["create_metrics_report", "create_comparison_report", "create_time_series_report"]
  }
};

export default function AgentsDropdown() {
  const [expandedAgents, setExpandedAgents] = useState<Record<string, boolean>>({});

  const toggleAgent = (agentKey: string) => {
    setExpandedAgents(prev => ({
      ...prev,
      [agentKey]: !prev[agentKey]
    }));
  };

  return (
    <div className="agents-dropdown">
      <div className="sidebar-section-title">Agents</div>
      <div className="agents-list">
        {Object.entries(AGENTS).map(([key, agent]) => (
          <div key={key} className="agent-dropdown-item">
            <button
              className={`agent-dropdown-header ${expandedAgents[key] ? "expanded" : ""}`}
              onClick={() => toggleAgent(key)}
            >
              <span className="agent-dropdown-arrow">
                {agent.tools.length > 0 ? (expandedAgents[key] ? "▼" : "▶") : "○"}
              </span>
              <span className="agent-dropdown-name">{agent.name}</span>
              {agent.tools.length > 0 && (
                <span className="agent-dropdown-count">{agent.tools.length}</span>
              )}
            </button>
            {expandedAgents[key] && agent.tools.length > 0 && (
              <div className="agent-tools-list">
                {agent.tools.map(tool => (
                  <div key={tool} className="agent-tool-item">
                    <span className="tool-icon">⚙</span>
                    <span className="tool-name">{tool}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
