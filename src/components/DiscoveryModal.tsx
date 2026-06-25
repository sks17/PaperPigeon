/**
 * DiscoveryModal — trigger on-demand ingestion of ANY research ecosystem.
 *
 * Submits {institution, topic, scrape} to the key-gated POST /api/discover (key stored in
 * localStorage), then polls GET /api/discover/{id} until the worker finishes and auto-selects the
 * resulting run via onDiscovered. A cached seed returns its run instantly. Monochrome / shadcn-native,
 * matching the other modals.
 */
import { useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { X, Compass, Loader2, AlertCircle } from 'lucide-react';
import { submitDiscovery, getDiscoveryStatus, type DiscoveryJob } from '../services/dynamodb';

const KEY_STORAGE = 'pp_discovery_key';

const STAGE_LABEL: Record<string, string> = {
  queued: 'Queued…',
  discovering: 'Discovering researchers…',
  describing: 'Writing grounded descriptions…',
  scraping: 'Scraping labs…',
  done: 'Done',
};

interface DiscoveryModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDiscovered: (runId: number) => void;
}

const DiscoveryModal: React.FC<DiscoveryModalProps> = ({ isOpen, onClose, onDiscovered }) => {
  const [institution, setInstitution] = useState('');
  const [topic, setTopic] = useState('');
  const [scrape, setScrape] = useState(false);
  const [apiKey, setApiKey] = useState(() => localStorage.getItem(KEY_STORAGE) ?? '');
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<DiscoveryJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  // Persist the key as typed so the user enters it once.
  useEffect(() => {
    localStorage.setItem(KEY_STORAGE, apiKey);
  }, [apiKey]);

  // Stop polling on unmount/close.
  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  if (!isOpen) return null;

  const finish = (runId: number) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    setBusy(false);
    onDiscovered(runId);
  };

  const startPolling = (jobId: number) => {
    pollRef.current = window.setInterval(async () => {
      try {
        const status = await getDiscoveryStatus(jobId, apiKey);
        setJob(status);
        if (status.status === 'succeeded' && status.run_id != null) finish(status.run_id);
        else if (status.status === 'failed') {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setBusy(false);
          setError(status.error || 'Discovery failed.');
        }
      } catch (e) {
        if (pollRef.current) window.clearInterval(pollRef.current);
        setBusy(false);
        setError(e instanceof Error ? e.message : 'Status check failed.');
      }
    }, 2500);
  };

  const handleSubmit = async () => {
    setError(null);
    setJob(null);
    if (!institution.trim()) return setError('Please enter an institution name.');
    if (!apiKey.trim()) return setError('Please enter your API key.');
    setBusy(true);
    try {
      const res = await submitDiscovery(
        { institution: institution.trim(), topic: topic.trim() || undefined, scrape },
        apiKey.trim(),
      );
      if (res.status === 'succeeded' && res.run_id != null) return finish(res.run_id); // cache hit
      setJob({
        id: res.job_id, status: res.status, stage: res.status, run_id: res.run_id,
        scrape, error: null, requested_at: null, finished_at: null,
      });
      startPolling(res.job_id);
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : 'Discovery failed.');
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={busy ? undefined : onClose} />
      <div className="relative z-10 w-full max-w-md mx-4">
        <Card className="shadow-2xl border bg-card">
          <CardHeader className="pb-3 relative">
            <button
              onClick={onClose}
              className="absolute top-4 right-4 p-2 hover:bg-muted rounded-full transition-colors"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-3 pr-10">
              <div className="w-10 h-10 rounded bg-primary flex items-center justify-center text-primary-foreground">
                <Compass className="w-5 h-5" />
              </div>
              <div>
                <h2 className="font-bold text-lg text-foreground">Discover an ecosystem</h2>
                <p className="text-xs text-muted-foreground">Any university or research network.</p>
              </div>
            </div>
          </CardHeader>

          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Institution</label>
              <Input
                placeholder="e.g. Massachusetts Institute of Technology"
                value={institution}
                onChange={(e) => setInstitution(e.target.value)}
                disabled={busy}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">Topic (optional)</label>
              <Input
                placeholder="e.g. robotics"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                disabled={busy}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-foreground select-none">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-border accent-primary"
                checked={scrape}
                onChange={(e) => setScrape(e.target.checked)}
                disabled={busy}
              />
              Also scrape lab pages <span className="text-xs text-muted-foreground">(slower)</span>
            </label>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">API key</label>
              <Input
                type="password"
                placeholder="X-Discovery-Key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                disabled={busy}
              />
            </div>

            {job && busy && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                {STAGE_LABEL[job.stage] ?? 'Working…'}
                <span className="text-muted-foreground/60">
                  This can take a minute; you can keep using the graph.
                </span>
              </div>
            )}
            {error && (
              <div className="flex items-start gap-2 text-sm text-red-600">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <Button onClick={handleSubmit} disabled={busy} className="w-full">
              {busy ? 'Discovering…' : 'Discover'}
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default DiscoveryModal;
