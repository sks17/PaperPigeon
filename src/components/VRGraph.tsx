import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { GraphData, Node as GraphNode, Link as GraphLink } from '../services/dynamodb';

// Import aframe before 3d-force-graph-vr
import 'aframe';
import ForceGraphVR, { type ForceGraphVRInstance } from '3d-force-graph-vr';

interface VRGraphProps {
  graphData: GraphData | null;
  loading?: boolean;
}

// 3d-force-graph mutates node objects (adds x/y/z) and link objects (source/target -> node refs),
// so the renderer is handed shallow COPIES to avoid corrupting the shared graphData.
type VRNode = GraphNode & { x?: number; y?: number; z?: number };

const VRGraph: React.FC<VRGraphProps> = ({ graphData, loading = false }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphVRInstance<VRNode, GraphLink> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isInitialized, setIsInitialized] = useState(false);

  useEffect(() => {
    if (!containerRef.current || !graphData) return;

    try {
      // Clean up existing graph
      if (graphRef.current) {
        graphRef.current._destructor?.();
        graphRef.current = null;
      }

      // Clear container completely
      containerRef.current.innerHTML = '';

      // Hand the renderer shallow copies — the force engine mutates these in place (node x/y/z,
      // link source/target), and the same graphData object backs the 2D view.
      const data = {
        nodes: graphData.nodes.map((node) => ({ ...node })) as VRNode[],
        links: graphData.links.map((link) => ({ ...link })),
      };

      // Let 3d-force-graph-vr run its normal 3D force layout (the previous build pinned fixed
      // positions AND zeroed the simulation ticks, so nodes never got rendered coordinates and the
      // scene was empty). val sizes labs larger than researchers; colors match the 2D view's intent.
      const graph = ForceGraphVR<VRNode, GraphLink>()(containerRef.current);
      graph
        .graphData(data)
        .nodeLabel((node) => node.name || node.id)
        .nodeColor((node) => (node.type === 'lab' ? '#22c55e' : '#f97316'))
        .nodeVal((node) => (node.type === 'lab' ? 8 : 4))
        .nodeRelSize(4)
        .nodeOpacity(0.95)
        .linkColor(() => '#9ca3af')
        .linkWidth(0.5)
        .linkOpacity(0.35);

      graphRef.current = graph;
      setIsInitialized(true);
      setError(null);
    } catch (err) {
      console.error('VR Graph initialization failed:', err);
      setError(err instanceof Error ? err.message : 'Failed to initialize VR mode');
    }

    return () => {
      if (graphRef.current) {
        graphRef.current._destructor?.();
        graphRef.current = null;
      }
    };
  }, [graphData]);

  // Loading state
  if (loading) {
    return (
      <div className="w-full h-screen flex items-center justify-center bg-black">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
          <p className="text-white">Loading VR Graph...</p>
        </div>
      </div>
    );
  }

  // No data state
  if (!graphData) {
    return (
      <div className="w-full h-screen flex items-center justify-center bg-black">
        <div className="text-center p-8 bg-gray-800 rounded-lg max-w-md">
          <div className="text-yellow-500 text-4xl mb-4">⚠️</div>
          <h2 className="text-xl font-semibold text-white mb-2">No Graph Data</h2>
          <p className="text-gray-400 mb-4">Graph data is required for VR mode.</p>
          <Link
            to="/app"
            className="inline-block px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
          >
            Return to 3D View
          </Link>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="w-full h-screen flex items-center justify-center bg-black">
        <div className="text-center p-8 bg-red-900/50 border border-red-500 rounded-lg max-w-md">
          <div className="text-red-500 text-4xl mb-4">⚠️</div>
          <h2 className="text-xl font-semibold text-white mb-2">VR Mode Error</h2>
          <p className="text-red-300 mb-4">{error}</p>
          <p className="text-gray-400 text-sm mb-4">
            VR mode requires WebXR support. Please use a compatible browser or VR headset.
          </p>
          <Link
            to="/app"
            className="inline-block px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
          >
            Return to 3D View
          </Link>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* VR Graph container - MUST be at root level, no background parents */}
      <div 
        ref={containerRef} 
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100vw',
          height: '100vh',
          zIndex: 1,
        }}
      />
      
      {/* UI Overlay - on top of A-Frame */}
      <div style={{ position: 'fixed', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 100 }}>
        {/* Exit VR button */}
        <div className="absolute top-4 left-4" style={{ pointerEvents: 'auto' }}>
          <Link
            to="/app"
            className="px-4 py-2 bg-gray-800/90 hover:bg-gray-700 text-white rounded-lg border border-gray-600 transition-colors flex items-center gap-2"
          >
            <span>←</span>
            <span>Exit VR Mode</span>
          </Link>
        </div>
        
        {/* Status indicator */}
        <div className="absolute top-4 right-4" style={{ pointerEvents: 'auto' }}>
          <div className={`px-3 py-1 rounded-full text-sm ${isInitialized ? 'bg-green-600' : 'bg-yellow-600'} text-white`}>
            {isInitialized ? `✓ ${graphData?.nodes?.length || 0} nodes loaded` : 'Initializing...'}
          </div>
        </div>

        {/* VR instructions */}
        <div className="absolute bottom-4 left-4 text-white/90 text-sm bg-black/70 p-3 rounded-lg max-w-xs" style={{ pointerEvents: 'auto' }}>
          <p className="font-semibold mb-1">VR Controls:</p>
          <ul className="text-xs space-y-1">
            <li>• Use WASD keys to move around</li>
            <li>• Click and drag to rotate view</li>
            <li>• Scroll to zoom in/out</li>
          </ul>
        </div>
      </div>
    </>
  );
};

export default VRGraph;

