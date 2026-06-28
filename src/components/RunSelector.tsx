/**
 * RunSelector — switch the graph between the published graph and a repopulation run snapshot.
 *
 * This is the UI entry point to grounded data: newly discovered/described researchers live on
 * repopulation runs, not on the published legacy graph, so without this picker they're unreachable.
 * Self-hides when there are no repopulation runs (e.g. against the legacy Flask backend, which has
 * no /api/runs), so it adds zero chrome until there's something to switch to.
 */
import { useEffect, useRef, useState } from 'react';
import { Layers, Check, ChevronDown } from 'lucide-react';
import { type RunSummary } from '../services/dynamodb';

function runLabel(r: RunSummary): string {
  const inst = r.seed?.institution;
  const topic = r.seed?.topic;
  if (inst) return topic ? `${inst} · ${topic}` : inst;
  return `Run #${r.id}`;
}

interface RunSelectorProps {
  runs: RunSummary[];
  value: number | null; // null = the published graph
  onChange: (runId: number | null) => void;
  /** 'pill' = standalone floating pill; 'inline' = bare trigger meant to sit inside another pill (e.g. the search bar). */
  variant?: 'pill' | 'inline';
}

const RunSelector: React.FC<RunSelectorProps> = ({ runs, value, onChange, variant = 'pill' }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // The legacy run is represented by "Published graph"; only real repopulation runs are snapshots.
  const snapshots = runs.filter((r) => r.seed?.source !== 'legacy_cache');

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  if (snapshots.length === 0) return null;

  const current = value == null ? null : snapshots.find((r) => r.id === value) ?? null;
  const label = current ? runLabel(current) : 'Published graph';

  const Option = ({
    active,
    onSelect,
    title,
    subtitle,
  }: {
    active: boolean;
    onSelect: () => void;
    title: string;
    subtitle?: string;
  }) => (
    <button
      onClick={onSelect}
      className="w-full flex items-start gap-2 px-3 py-2 rounded-lg text-left hover:bg-muted transition-colors"
    >
      <Check
        className={`w-4 h-4 mt-0.5 shrink-0 ${active ? 'opacity-100' : 'opacity-0'}`}
      />
      <span className="min-w-0">
        <span className="block text-sm text-foreground truncate">{title}</span>
        {subtitle && (
          <span className="block text-xs text-muted-foreground truncate">{subtitle}</span>
        )}
      </span>
    </button>
  );

  const inline = variant === 'inline';

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={inline ? `Data source: ${label}` : undefined}
        className={
          inline
            ? 'flex items-center gap-1.5 max-w-[11rem] text-sm text-foreground/90 hover:text-foreground transition-colors'
            : 'flex items-center gap-2 px-4 py-2 rounded-full bg-white/95 backdrop-blur border shadow hover:shadow-lg transition-all duration-200 text-sm max-w-[19rem]'
        }
      >
        <Layers className="w-4 h-4 shrink-0 text-muted-foreground" />
        <span className="font-medium truncate">{label}</span>
        <ChevronDown
          className={`w-4 h-4 shrink-0 text-muted-foreground transition-transform duration-200 ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-80 max-h-[60vh] overflow-y-auto rounded-xl border bg-card shadow-xl p-1.5">
          <Option
            active={value == null}
            onSelect={() => {
              onChange(null);
              setOpen(false);
            }}
            title="Published graph"
            subtitle="The live research network"
          />
          <div className="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wider text-muted-foreground/70">
            Run snapshots
          </div>
          {snapshots.map((r) => (
            <Option
              key={r.id}
              active={value === r.id}
              onSelect={() => {
                onChange(r.id);
                setOpen(false);
              }}
              title={runLabel(r)}
              subtitle={`${r.nodes} nodes · ${r.status}${r.published ? ' · published' : ''}`}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export default RunSelector;
