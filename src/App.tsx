/**
 * Paper Pigeon - Root Application Component
 *
 * Handles routing between the main 3D graph view and VR mode.
 * Graph data is fetched once at the app level and passed down to child routes.
 */
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { useState, useEffect } from 'react'
import ResearchNetworkGraph from './components/ResearchNetworkGraph'
import VRGraph from './components/VRGraph'
import { AccessibilityProvider } from './contexts/AccessibilityContext'
import { fetchGraphData, fetchRuns, type GraphData, type RunSummary } from './services/dynamodb'

function App() {
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  // null = the published graph; a run id = that repopulation run's snapshot (where grounded data lives).
  const [runId, setRunId] = useState<number | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])

  // Discover available run snapshots once (silently empty against backends without /api/runs).
  useEffect(() => {
    fetchRuns().then(setRuns).catch(() => setRuns([]))
  }, [])

  // (Re)load the graph whenever the selected run changes.
  useEffect(() => {
    let active = true
    setLoading(true)
    fetchGraphData(runId ?? undefined)
      .then((data) => active && setGraphData(data))
      .catch((err) => {
        console.error('Failed to load graph data:', err)
        if (active) setGraphData({ nodes: [], links: [] })
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [runId])

  return (
    <BrowserRouter>
      <AccessibilityProvider>
        <Routes>
          <Route path="/" element={
            <div className="w-full h-screen">
              <ResearchNetworkGraph
                graphData={graphData}
                loading={loading}
                runs={runs}
                runId={runId}
                onRunChange={setRunId}
              />
            </div>
          } />
          <Route path="/vr" element={
            <VRGraph graphData={graphData} loading={loading} />
          } />
        </Routes>
      </AccessibilityProvider>
    </BrowserRouter>
  )
}

export default App