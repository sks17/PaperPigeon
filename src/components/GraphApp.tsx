/**
 * GraphApp — layout route that owns the graph data + run state for the actual application (the 3-D
 * graph at /app and VR at /vr). Mounted ONLY under those routes via <Outlet>, so the marketing
 * landing/docs pages never pull in the graph or its 3-D libraries. Children read state through
 * `useGraph()`. Data is fetched once and shared across /app and /vr (no refetch when toggling VR).
 */
import { Outlet, useOutletContext } from 'react-router-dom';
import { useState, useEffect } from 'react';
import ResearchNetworkGraph from './ResearchNetworkGraph';
import VRGraph from './VRGraph';
import { fetchGraphData, fetchRuns, type GraphData, type RunSummary } from '../services/dynamodb';

interface GraphContext {
  graphData: GraphData | null;
  loading: boolean;
  runId: number | null;
  runs: RunSummary[];
  setRunId: (id: number | null) => void;
  onDiscovered: (id: number) => void;
}

export function GraphApp() {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  // null = the published graph; a run id = that repopulation run's snapshot (where grounded data lives).
  const [runId, setRunId] = useState<number | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);

  // Discover available run snapshots once (silently empty against backends without /api/runs).
  useEffect(() => {
    fetchRuns().then(setRuns).catch(() => setRuns([]));
  }, []);

  // After a discovery job finishes: refresh the run list and switch to the new run.
  const onDiscovered = (newRunId: number) => {
    fetchRuns().then(setRuns).catch(() => setRuns([]));
    setRunId(newRunId);
  };

  // (Re)load the graph whenever the selected run changes.
  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchGraphData(runId ?? undefined)
      .then((data) => active && setGraphData(data))
      .catch((err) => {
        console.error('Failed to load graph data:', err);
        if (active) setGraphData({ nodes: [], links: [] });
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [runId]);

  const ctx: GraphContext = { graphData, loading, runId, runs, setRunId, onDiscovered };
  return <Outlet context={ctx} />;
}

const useGraph = () => useOutletContext<GraphContext>();

export function GraphScreen() {
  const g = useGraph();
  return (
    <div className="w-full h-screen">
      <ResearchNetworkGraph
        graphData={g.graphData}
        loading={g.loading}
        runs={g.runs}
        runId={g.runId}
        onRunChange={g.setRunId}
        onDiscovered={g.onDiscovered}
      />
    </div>
  );
}

export function VrScreen() {
  const g = useGraph();
  return <VRGraph graphData={g.graphData} loading={g.loading} />;
}
