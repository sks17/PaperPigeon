/**
 * Paper Pigeon — Root Application Component
 *
 * Routes:
 *   /      → marketing landing (front door, text-only)
 *   /docs  → release notes / design docs
 *   /app   → the 3-D research graph (the application)
 *   /vr    → immersive VR view of the graph
 *
 * The landing + docs are eager and tiny. The graph (and its heavy 3-D / VR libraries) is lazily
 * code-split behind /app and /vr, so the front door loads as fast as plain text. Graph data + run
 * state live in GraphApp, shared across /app and /vr.
 */
import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AccessibilityProvider } from './contexts/AccessibilityContext'
import Landing from './pages/Landing'
import Docs from './pages/Docs'

const GraphApp = lazy(() => import('./components/GraphApp').then((m) => ({ default: m.GraphApp })))
const GraphScreen = lazy(() => import('./components/GraphApp').then((m) => ({ default: m.GraphScreen })))
const VrScreen = lazy(() => import('./components/GraphApp').then((m) => ({ default: m.VrScreen })))

function GraphLoading() {
  return (
    <div className="flex h-screen w-full items-center justify-center bg-white">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-700" />
    </div>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AccessibilityProvider>
        <Suspense fallback={<GraphLoading />}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/docs" element={<Docs />} />
            <Route element={<GraphApp />}>
              <Route path="/app" element={<GraphScreen />} />
              <Route path="/vr" element={<VrScreen />} />
            </Route>
          </Routes>
        </Suspense>
      </AccessibilityProvider>
    </BrowserRouter>
  )
}

export default App
