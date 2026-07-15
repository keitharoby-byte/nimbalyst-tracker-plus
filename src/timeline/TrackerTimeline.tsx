import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useEditorLifecycle } from '@nimbalyst/extension-sdk';
import type { EditorHostProps } from '@nimbalyst/extension-sdk';

import {
  RELATIONSHIP_LABELS,
  dayNumber,
  dayToIso,
  deriveTimelineRange,
  emptyTimelineDocument,
  itemReference,
  milestoneSummaries,
  parseTimelineDocument,
  relationshipLabel,
} from './model';
import type {
  MilestoneSummary,
  RelationshipType,
  TimelineDocument,
  TimelineItem,
  TimelineMode,
  TimelineRelationship,
  TimelineZoom,
  ValidationFinding,
} from './types';

const PIXELS_PER_DAY: Record<TimelineZoom, number> = { day: 30, week: 12, month: 5 };
const RELATIONSHIP_TYPES = Object.keys(RELATIONSHIP_LABELS) as RelationshipType[];

export function TrackerTimeline({ host }: EditorHostProps) {
  const documentRef = useRef<TimelineDocument>(emptyTimelineDocument());
  const [document, setDocument] = useState<TimelineDocument>(documentRef.current);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { isLoading, error, markDirty } = useEditorLifecycle(host, {
    applyContent: (value: TimelineDocument) => {
      documentRef.current = value;
      setDocument(value);
      setSelectedId((current) => current && value.snapshot.items.some((item) => item.id === current) ? current : null);
    },
    getCurrentContent: () => documentRef.current,
    parse: parseTimelineDocument,
    serialize: (value) => JSON.stringify(value, null, 2),
  });

  const updateDocument = useCallback((updater: (current: TimelineDocument) => TimelineDocument) => {
    const next = updater(documentRef.current);
    documentRef.current = next;
    setDocument(next);
    markDirty();
  }, [markDirty]);

  const setMode = useCallback((mode: TimelineMode) => {
    updateDocument((current) => ({ ...current, view: { ...current.view, mode } }));
  }, [updateDocument]);

  const setZoom = useCallback((zoom: TimelineZoom) => {
    updateDocument((current) => ({ ...current, view: { ...current.view, zoom, fitToWidth: false } }));
  }, [updateDocument]);

  const toggleFitToWidth = useCallback(() => {
    updateDocument((current) => ({ ...current, view: { ...current.view, fitToWidth: !current.view.fitToWidth } }));
  }, [updateDocument]);

  const toggleCompactRows = useCallback(() => {
    updateDocument((current) => ({ ...current, view: { ...current.view, compactRows: !current.view.compactRows } }));
  }, [updateDocument]);

  const filteredItems = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return document.snapshot.items;
    return document.snapshot.items.filter((item) => [
      item.title,
      item.issueKey,
      item.primaryType,
      item.workflow,
      item.scheduleHealth,
      item.executionConstraint,
      item.riskLevel,
      item.ownerLabel,
      ...item.typeTags,
    ].some((value) => String(value ?? '').toLowerCase().includes(needle)));
  }, [document.snapshot.items, search]);

  const selected = useMemo(
    () => document.snapshot.items.find((item) => item.id === selectedId) ?? null,
    [document.snapshot.items, selectedId],
  );

  if (error) return <div className="nt-error">{error.message}</div>;
  if (isLoading) return <div className="nt-loading">Loading tracker timeline…</div>;

  const { snapshot } = document;
  const attention = snapshot.items.filter((item) => item.scheduleHealth !== 'on-track').length;
  const blocked = snapshot.items.filter((item) => item.executionConstraint === 'blocked').length;
  const errors = snapshot.validation.filter((finding) => finding.severity === 'error').length;
  const truncated = snapshot.page.queryTruncated || snapshot.page.responseTruncated;

  return (
    <div className="nt-shell">
      <header className="nt-toolbar">
        <div className="nt-title-group">
          <span className="nt-product-mark">TD</span>
          <div>
            <div className="nt-title">{document.title}</div>
            <div className="nt-subtitle">Topology, forecast health, constraints, and durable risk</div>
          </div>
        </div>
        <nav className="nt-tabs" aria-label="Timeline views">
          {(['timeline', 'graph', 'report'] as TimelineMode[]).map((mode) => (
            <button key={mode} className={document.view.mode === mode ? 'active' : ''} onClick={() => setMode(mode)}>
              {mode === 'report' ? 'Reports' : titleCase(mode)}
            </button>
          ))}
        </nav>
        <div className="nt-toolbar-actions">
          {document.view.mode === 'timeline' && (
            <div className="nt-segmented" aria-label="Timeline zoom">
              {(['day', 'week', 'month'] as TimelineZoom[]).map((zoom) => (
                <button key={zoom} className={!document.view.fitToWidth && document.view.zoom === zoom ? 'active' : ''} onClick={() => setZoom(zoom)}>
                  {titleCase(zoom)}
                </button>
              ))}
              <button className={`nt-fit-button ${document.view.fitToWidth ? 'active' : ''}`} onClick={toggleFitToWidth} aria-pressed={document.view.fitToWidth} title="Resize the date scale to the available width">
                Fit
              </button>
              <button className={`nt-compact-button ${document.view.compactRows ? 'active' : ''}`} onClick={toggleCompactRows} aria-pressed={document.view.compactRows} title="Collapse timeline rows">
                Compact
              </button>
            </div>
          )}
          <input className="nt-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter trackers…" aria-label="Filter tracker items" />
        </div>
      </header>

      <div className="nt-metrics">
        <Metric label="Items" value={snapshot.items.length} />
        <Metric label="Milestones" value={snapshot.milestones.length} tone="purple" />
        <Metric label="Typed edges" value={snapshot.relationships.length} tone="blue" />
        <Metric label="At risk / late" value={attention} tone="amber" />
        <Metric label="Execution blocked" value={blocked} tone="red" />
        <Metric label="Validation errors" value={errors} tone="red" />
        {truncated && <span className="nt-warning">Snapshot is bounded; refine filters to see all items.</span>}
      </div>

      <Watermark document={document} />

      <main className="nt-main">
        <section className="nt-workspace">
          {snapshot.items.length === 0 ? (
            <EmptyState />
          ) : document.view.mode === 'timeline' ? (
            <TimelineView items={filteredItems} zoom={document.view.zoom} fitToWidth={document.view.fitToWidth} compactRows={document.view.compactRows} onSelect={setSelectedId} selectedId={selectedId} />
          ) : document.view.mode === 'graph' ? (
            <GraphView items={filteredItems} relationships={snapshot.relationships} onSelect={setSelectedId} selectedId={selectedId} />
          ) : (
            <ReportView document={document} onSelect={setSelectedId} />
          )}
        </section>
        <ItemDetails item={selected} relationships={snapshot.relationships} items={snapshot.items} onSelect={setSelectedId} />
      </main>
    </div>
  );
}

function Metric({ label, value, tone = 'neutral' }: { label: string; value: number; tone?: string }) {
  return <div className={`nt-metric tone-${tone}`}><strong>{value}</strong><span>{label}</span></div>;
}

function Watermark({ document }: { document: TimelineDocument }) {
  const source = document.snapshot.source;
  const fingerprint = typeof source.schemaFingerprint === 'string' ? source.schemaFingerprint.slice(0, 12) : 'unavailable';
  const revision = typeof source.projectStateRevision === 'string' ? source.projectStateRevision : 'unavailable';
  return (
    <div className="nt-watermark" aria-label="Projection provenance">
      <span>Snapshot <strong>{document.snapshot.generatedAt ? formatDateTime(document.snapshot.generatedAt) : 'not synced'}</strong></span>
      <span>Schema <code>{fingerprint}</code></span>
      <span>ProjectState <code>{revision}</code></span>
      <span>Projection v{document.version}</span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="nt-empty">
      <div className="nt-empty-icon">◇</div>
      <h2>Sync native tracker data</h2>
      <p>Run <code>native_tracker_sync_timeline</code>. Tracker+ will project normalized edge records, critical path, schedule health, execution constraints, risk, and validation findings here.</p>
    </div>
  );
}

function TimelineView({ items, zoom, fitToWidth, compactRows, selectedId, onSelect }: {
  items: TimelineItem[];
  zoom: TimelineZoom;
  fitToWidth: boolean;
  compactRows: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const scheduled = items.filter((item) => item.startDate || item.dueDate || item.forecastDate);
  const unscheduled = items.filter((item) => !item.startDate && !item.dueDate && !item.forecastDate);
  const range = useMemo(() => deriveTimelineRange(scheduled), [scheduled]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return undefined;
    const updateWidth = () => setViewportWidth(element.clientWidth);
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const rangeDays = Math.max(1, range.end - range.start + 1);
  const availableGridWidth = Math.max(360, viewportWidth - 320);
  const pxPerDay = fitToWidth && viewportWidth > 0
    ? Math.max(2, availableGridWidth / rangeDays)
    : PIXELS_PER_DAY[zoom];
  const gridWidth = fitToWidth && viewportWidth > 0
    ? availableGridWidth
    : Math.max(720, rangeDays * pxPerDay);
  const effectiveZoom: TimelineZoom = fitToWidth
    ? pxPerDay >= 20 ? 'day' : pxPerDay >= 7 ? 'week' : 'month'
    : zoom;
  const tickStep = effectiveZoom === 'day' ? 1 : effectiveZoom === 'week' ? 7 : 30;
  const ticks: number[] = [];
  for (let day = range.start; day <= range.end; day += tickStep) ticks.push(day);

  return (
    <div ref={scrollRef} className={`nt-timeline-scroll ${compactRows ? 'compact' : ''} ${fitToWidth ? 'fit-width' : ''}`}>
      <div className="nt-state-legend">
        <span><i className="health-on-track" />On track</span>
        <span><i className="health-at-risk" />At risk</span>
        <span><i className="health-late" />Late</span>
        <span><b />Bar color = schedule health; text = workflow / constraint</span>
      </div>
      <div className="nt-timeline-table" style={{ width: gridWidth + 320 }}>
        <div className="nt-timeline-header nt-sticky-label">Tracker item</div>
        <div className="nt-timeline-header nt-time-header" style={{ width: gridWidth }}>
          {ticks.map((day) => <div key={day} className="nt-time-tick" style={{ left: (day - range.start) * pxPerDay }}>{formatTick(dayToIso(day), effectiveZoom)}</div>)}
        </div>
        {scheduled.map((item) => {
          const start = dayNumber(item.startDate) ?? dayNumber(item.dueDate) ?? dayNumber(item.forecastDate) ?? range.start;
          const targetEnd = dayNumber(item.dueDate) ?? start;
          const forecastEnd = dayNumber(item.forecastDate);
          const end = Math.max(targetEnd, forecastEnd ?? targetEnd);
          const left = Math.max(0, (start - range.start) * pxPerDay);
          const width = Math.max(item.primaryType === 'milestone' ? 14 : pxPerDay, (end - start + 1) * pxPerDay);
          const forecastLeft = forecastEnd === null ? null : Math.max(0, (forecastEnd - range.start) * pxPerDay);
          return (
            <React.Fragment key={item.id}>
              <button className={`nt-row-label nt-sticky-label ${selectedId === item.id ? 'selected' : ''}`} onClick={() => onSelect(item.id)}>
                <span className={`nt-type-dot workflow-${slug(item.workflow)}`} />
                <span className="nt-row-copy"><strong>{item.title}</strong><small>{itemReference(item)} · {item.workflow || 'unset'} / {item.executionConstraint}</small></span>
                {item.isCritical && <span className="nt-critical-badge">critical path</span>}
              </button>
              <div className="nt-grid-row" style={{ width: gridWidth, '--nt-day-width': `${pxPerDay}px` } as React.CSSProperties} onClick={() => onSelect(item.id)}>
                {item.primaryType === 'milestone' ? (
                  <div className={`nt-milestone-marker health-${item.scheduleHealth}`} style={{ left }} title={`${item.title} · ${item.dueDate ?? item.startDate}`} />
                ) : (
                  <div className={`nt-gantt-bar health-${item.scheduleHealth}`} style={{ left, width }} title={`${item.title}: ${item.startDate ?? '…'} → ${item.dueDate ?? '…'}; forecast ${item.forecastDate ?? 'unset'}`}>
                    <span className="nt-gantt-progress" style={{ width: `${item.progress ?? 0}%` }} />
                    <span className="nt-gantt-label">{item.progress == null ? '' : `${Math.round(item.progress)}%`}</span>
                  </div>
                )}
                {forecastLeft !== null && forecastEnd !== targetEnd && <span className="nt-forecast-marker" style={{ left: forecastLeft }} title={`Forecast ${item.forecastDate}`} />}
              </div>
            </React.Fragment>
          );
        })}
      </div>
      {unscheduled.length > 0 && (
        <div className="nt-unscheduled">
          <h3>Unscheduled <span>{unscheduled.length}</span></h3>
          <div className="nt-chip-list">{unscheduled.map((item) => <button key={item.id} onClick={() => onSelect(item.id)} className={selectedId === item.id ? 'selected' : ''}><span>{item.title}</span><small>{itemReference(item)} · {item.scheduleHealth}</small></button>)}</div>
        </div>
      )}
    </div>
  );
}

function GraphView({ items, relationships, selectedId, onSelect }: {
  items: TimelineItem[];
  relationships: TimelineRelationship[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const visible = items.slice(0, 120);
  const positions = useMemo(() => {
    const result = new Map<string, { x: number; y: number }>();
    const milestones = visible.filter((item) => item.primaryType === 'milestone');
    const others = visible.filter((item) => item.primaryType !== 'milestone');
    milestones.forEach((item, index) => result.set(item.id, { x: 140 + index * 250, y: 80 }));
    others.forEach((item, index) => result.set(item.id, { x: 140 + (index % 5) * 250, y: 230 + Math.floor(index / 5) * 145 }));
    return result;
  }, [visible]);
  const width = Math.max(1_250, 270 + Math.max(0, ...[...positions.values()].map((position) => position.x)));
  const height = Math.max(520, 170 + Math.max(0, ...[...positions.values()].map((position) => position.y)));
  const edges = relationships.filter((edge) => positions.has(edge.sourceId) && positions.has(edge.targetId));

  return (
    <div className="nt-graph-wrap">
      <div className="nt-legend">
        {RELATIONSHIP_TYPES.map((type) => <span key={type} className={`edge-${type}`}><i />{RELATIONSHIP_LABELS[type].forward}</span>)}
      </div>
      <div className="nt-graph-note">Node border = schedule health · node dot = workflow · edge color = relationship type</div>
      <svg className="nt-graph" width={width} height={height} role="img" aria-label="Tracker relationship graph">
        <defs>{RELATIONSHIP_TYPES.map((type) => <marker key={type} id={`arrow-${type}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" className={`fill-${type}`} /></marker>)}</defs>
        {edges.map((edge) => {
          const source = positions.get(edge.sourceId)!;
          const target = positions.get(edge.targetId)!;
          return <line key={edge.id} x1={source.x} y1={source.y} x2={target.x} y2={target.y} className={`nt-edge edge-${edge.relationshipType} state-${edge.state}`} markerEnd={edge.directed ? `url(#arrow-${edge.relationshipType})` : undefined} />;
        })}
        {visible.map((item) => {
          const position = positions.get(item.id)!;
          const isMilestone = item.primaryType === 'milestone';
          return (
            <g key={item.id} className={`nt-node health-${item.scheduleHealth} ${selectedId === item.id ? 'selected' : ''}`} transform={`translate(${position.x - 105} ${position.y - 38})`} onClick={() => onSelect(item.id)} role="button">
              <rect width="210" height="76" rx={isMilestone ? 18 : 9} className={isMilestone ? 'milestone' : ''} />
              <circle cx="16" cy="20" r="5" className={`workflow-${slug(item.workflow)}`} />
              <text x="28" y="24" className="nt-node-title">{truncate(item.title, 25)}</text>
              <text x="14" y="49" className="nt-node-meta">{itemReference(item)} · {item.workflow || 'unset'}</text>
              <text x="14" y="65" className="nt-node-risk">{item.scheduleHealth} · risk {item.riskLevel}</text>
            </g>
          );
        })}
      </svg>
      {items.length > visible.length && <div className="nt-graph-limit">Showing the first {visible.length} filtered items.</div>}
    </div>
  );
}

function ReportView({ document, onSelect }: { document: TimelineDocument; onSelect: (id: string) => void }) {
  const summaries = useMemo(() => milestoneSummaries(document.snapshot), [document.snapshot]);
  const { validation, criticalPath } = document.snapshot;
  return (
    <div className="nt-reports">
      <div className="nt-report-intro">
        <div><h2>Milestone forecast and controls</h2><p>Schedule health is separate from workflow and execution blockage; risk is matrix-derived with deterministic escalation floors.</p></div>
        <span>{validation.filter((finding) => finding.severity === 'error').length} validation errors</span>
      </div>
      {summaries.length ? <div className="nt-report-grid">{summaries.map((summary) => <MilestoneCard key={summary.milestone.id} summary={summary} onSelect={onSelect} />)}</div> : <div className="nt-empty compact"><h2>No milestones yet</h2><p>Create a native <code>milestone</code> tracker item, then sync.</p></div>}
      <section className="nt-analysis-grid">
        <article className="nt-analysis-card">
          <h3>Critical path</h3>
          <p><strong>{criticalPath.durationDays}</strong> calculated days across active hard-serial edges.</p>
          {criticalPath.cycleItemIds.length > 0 ? <p className="nt-danger">Cycle detected; path calculation suspended.</p> : <div className="nt-chip-list">{criticalPath.itemIds.map((id) => { const item = document.snapshot.items.find((entry) => entry.id === id); return item ? <button key={id} onClick={() => onSelect(id)}>{item.title}<small>{item.criticalPathSlackDays}d slack</small></button> : null; })}</div>}
        </article>
        <ValidationPanel findings={validation} onSelect={onSelect} />
      </section>
    </div>
  );
}

function MilestoneCard({ summary, onSelect }: { summary: MilestoneSummary; onSelect: (id: string) => void }) {
  const { milestone } = summary;
  return (
    <article className={`nt-report-card health-${summary.scheduleHealth}`}>
      <header><button onClick={() => onSelect(milestone.id)}>{milestone.title}</button><span>{summary.scheduleHealth}</span></header>
      <div className="nt-dimension-row"><span>Workflow <strong>{milestone.workflow || 'unset'}</strong></span><span>Constraint <strong>{milestone.executionConstraint}</strong></span><span>Risk <strong className={`risk-${summary.riskLevel}`}>{summary.riskLevel}</strong></span></div>
      <div className="nt-report-meta"><strong>{Math.round(summary.progress)}%</strong><span>Target {milestone.dueDate || 'not scheduled'} · Forecast {milestone.forecastDate || 'unset'}</span></div>
      <div className="nt-progress-track"><i style={{ width: `${summary.progress}%` }} /></div>
      <dl>
        <div><dt>Deliverables</dt><dd>{summary.complete}/{summary.deliverables.length}</dd></div>
        <div><dt>Dependencies</dt><dd>{summary.activeDependencies.length}</dd></div>
        <div><dt>Blocked</dt><dd>{summary.blockedItems.length}</dd></div>
        <div><dt>Overdue</dt><dd>{summary.overdue.length}</dd></div>
      </dl>
      {summary.blockedItems.length > 0 && <div className="nt-attention-list"><strong>Execution blocked</strong>{summary.blockedItems.slice(0, 4).map((item) => <button key={item.id} onClick={() => onSelect(item.id)}>{item.title}</button>)}</div>}
    </article>
  );
}

function ValidationPanel({ findings, onSelect }: { findings: ValidationFinding[]; onSelect: (id: string) => void }) {
  return (
    <article className="nt-analysis-card nt-validation-card">
      <h3>Validation</h3>
      {!findings.length ? <p>No governance findings in this snapshot.</p> : findings.map((finding, index) => (
        <div key={`${finding.code}-${index}`} className={`nt-finding severity-${finding.severity}`}>
          <span>{finding.severity}</span><strong>{finding.code}</strong><p>{finding.message}</p>
          {finding.itemIds.map((id) => <button key={id} onClick={() => onSelect(id)}>{id}</button>)}
        </div>
      ))}
    </article>
  );
}

function ItemDetails({ item, relationships, items, onSelect }: {
  item: TimelineItem | null;
  relationships: TimelineRelationship[];
  items: TimelineItem[];
  onSelect: (id: string) => void;
}) {
  const itemsById = useMemo(() => new Map(items.map((entry) => [entry.id, entry])), [items]);
  if (!item) return <aside className="nt-details nt-details-empty"><span>Select an item</span><p>Inspect independent state dimensions, derived risk, slack, and normalized relationships.</p></aside>;
  const outgoing = relationships.filter((edge) => edge.sourceId === item.id);
  const incoming = relationships.filter((edge) => edge.targetId === item.id);
  return (
    <aside className="nt-details">
      <span className="nt-kicker">{item.primaryType}</span>
      <h2>{item.title}</h2>
      <a className="nt-tracker-link" href={`nimbalyst://${encodeURIComponent(itemReference(item))}`}>{itemReference(item)} ↗</a>
      <div className="nt-state-stack">
        <StateLine label="Workflow" value={item.workflow || 'Not set'} className={`workflow-${slug(item.workflow)}`} />
        <StateLine label="Schedule" value={item.scheduleHealth} className={`health-${item.scheduleHealth}`} />
        <StateLine label="Constraint" value={item.executionConstraint} className={`constraint-${item.executionConstraint}`} />
        <StateLine label="Risk" value={item.riskLevel} className={`risk-${item.riskLevel}`} />
      </div>
      <dl className="nt-detail-grid">
        <div><dt>Progress</dt><dd>{item.progress == null ? 'Not set' : `${Math.round(item.progress)}%`}</dd></div>
        <div><dt>Owner</dt><dd>{item.ownerLabel || 'Unassigned'}</dd></div>
        <div><dt>Start</dt><dd>{item.startDate || 'Not set'}</dd></div>
        <div><dt>Target</dt><dd>{item.dueDate || 'Not set'}</dd></div>
        <div><dt>Forecast</dt><dd>{item.forecastDate || 'Not set'}</dd></div>
        <div><dt>Schedule slack</dt><dd>{item.scheduleSlackDays == null ? 'Unknown' : `${item.scheduleSlackDays}d`}</dd></div>
        <div><dt>Impact × likelihood</dt><dd>{item.impact ?? '–'} × {item.likelihood ?? '–'} = {item.riskScore ?? '–'}</dd></div>
        <div><dt>Critical-path slack</dt><dd>{item.criticalPathSlackDays == null ? 'N/A' : `${item.criticalPathSlackDays}d`}</dd></div>
      </dl>
      {item.scheduleHealthReasons.length > 0 && <ReasonList title="Schedule rationale" entries={item.scheduleHealthReasons} />}
      {item.riskReasons.length > 0 && <ReasonList title="Risk rationale" entries={item.riskReasons} />}
      <RelationshipList title="Links from this item" edges={outgoing} itemsById={itemsById} inverse={false} onSelect={onSelect} />
      <RelationshipList title="Derived backlinks" edges={incoming} itemsById={itemsById} inverse onSelect={onSelect} />
    </aside>
  );
}

function StateLine({ label, value, className }: { label: string; value: string; className: string }) {
  return <div><span>{label}</span><strong className={className}>{value}</strong></div>;
}

function ReasonList({ title, entries }: { title: string; entries: string[] }) {
  return <div className="nt-reason-list"><h3>{title}</h3><ul>{entries.map((entry) => <li key={entry}>{entry}</li>)}</ul></div>;
}

function RelationshipList({ title, edges, itemsById, inverse, onSelect }: {
  title: string;
  edges: TimelineRelationship[];
  itemsById: Map<string, TimelineItem>;
  inverse: boolean;
  onSelect: (id: string) => void;
}) {
  if (!edges.length) return null;
  return (
    <div className="nt-relationship-list"><h3>{title}</h3>{edges.map((edge) => {
      const id = inverse ? edge.sourceId : edge.targetId;
      const linked = itemsById.get(id);
      const titleValue = linked?.title || (inverse ? edge.sourceTitle || edge.sourceIssueKey : edge.targetTitle || edge.targetIssueKey) || id;
      const controls = edge.relationshipType === 'depends-on'
        ? [edge.dependencyMode, edge.hardness, edge.leadLagDays ? `${edge.leadLagDays}d` : null].filter(Boolean).join(' · ')
        : edge.state;
      return <button key={edge.id} onClick={() => linked && onSelect(id)} disabled={!linked}><i className={`edge-${edge.relationshipType}`} /><span><strong>{titleValue}</strong><small>{relationshipLabel(edge, inverse)} · {controls || edge.state}</small></span></button>;
    })}</div>
  );
}

function titleCase(value: string): string { return value.charAt(0).toUpperCase() + value.slice(1); }
function truncate(value: string, length: number): string { return value.length <= length ? value : `${value.slice(0, length - 1)}…`; }
function slug(value: string | null | undefined): string { return String(value || 'unset').toLowerCase().replaceAll(/[^a-z0-9]+/g, '-'); }
function formatDateTime(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); }
function formatTick(value: string, zoom: TimelineZoom): string { const date = new Date(`${value}T00:00:00Z`); if (zoom === 'month') return date.toLocaleDateString(undefined, { month: 'short', year: '2-digit', timeZone: 'UTC' }); if (zoom === 'week') return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }); return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', timeZone: 'UTC' }); }
