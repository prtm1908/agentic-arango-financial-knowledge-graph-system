"use client";

type Metric = {
  metric_name: string;
  value: number | null;
  unit?: string;
  denomination?: string;
  confidence?: number;
  source_pages?: number[];
  notes?: string;
};

type ResultsPanelProps = {
  metrics: Metric[];
  finalResult: any;
  error: string | null;
  jobId: string | null;
  isRunning: boolean;
};

const formatMetricValue = (metric: Metric) => {
  if (metric.value === null || metric.value === undefined) {
    return "Not found";
  }
  const unit = metric.unit ? ` ${metric.unit}` : "";
  const denom = metric.denomination ? ` (${metric.denomination})` : "";
  return `${metric.value}${unit}${denom}`;
};

export default function ResultsPanel({
  metrics,
  finalResult,
  error,
  jobId,
  isRunning
}: ResultsPanelProps) {
  return (
    <section className="panel" style={{ animationDelay: "0.22s" }}>
      <div className="panel-header">
        <h2>Results</h2>
        <span className="status-pill">{isRunning ? "Processing" : "Ready"}</span>
      </div>

      <div className="results-stack">
        <div className="result-block">
          <strong>Session</strong>
          <div>Job ID: {jobId || "-"}</div>
          <div>Status: {isRunning ? "In progress" : "Idle"}</div>
        </div>

        {error && (
          <div className="result-block">
            <strong>Error</strong>
            <div>{error}</div>
          </div>
        )}

        {metrics.length > 0 && (
          <div>
            <h3>Metrics Found</h3>
            <div className="metrics-grid">
              {metrics.map((metric, index) => (
                <div key={`${metric.metric_name}-${index}`} className="metric-card">
                  <div className="metric-title">{metric.metric_name}</div>
                  <div className="metric-value">{formatMetricValue(metric)}</div>
                  <div className="metric-meta">
                    Confidence: {metric.confidence ?? "-"}
                  </div>
                  <div className="metric-meta">
                    Pages: {metric.source_pages?.join(", ") || "-"}
                  </div>
                  {metric.notes ? <div className="metric-meta">{metric.notes}</div> : null}
                </div>
              ))}
            </div>
          </div>
        )}

        {finalResult && (
          <div>
            <h3>Final Output</h3>
            <div className="result-block">
              <pre className="result-code">{JSON.stringify(finalResult, null, 2)}</pre>
            </div>
          </div>
        )}

        {!finalResult && metrics.length === 0 && !error && (
          <div className="result-block">
            <strong>Waiting for results</strong>
            <div>Metrics and graph answers will appear here as soon as they are ready.</div>
          </div>
        )}
      </div>
    </section>
  );
}
