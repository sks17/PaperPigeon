// Type declarations for VR libraries

declare module 'aframe' {
  // A-Frame is imported for side effects only
  const aframe: unknown;
  export default aframe;
}

declare module '3d-force-graph-vr' {
  export interface ForceGraphVRInstance<N = unknown, L = unknown> {
    (element: HTMLElement): ForceGraphVRInstance<N, L>;
    graphData(data: { nodes: N[]; links: L[] }): ForceGraphVRInstance<N, L>;
    nodeLabel(accessor: string | ((node: N) => string)): ForceGraphVRInstance<N, L>;
    nodeColor(accessor: string | ((node: N) => string)): ForceGraphVRInstance<N, L>;
    nodeVal(accessor: string | number | ((node: N) => number)): ForceGraphVRInstance<N, L>;
    nodeRelSize(size: number): ForceGraphVRInstance<N, L>;
    nodeOpacity(opacity: number): ForceGraphVRInstance<N, L>;
    linkColor(accessor: string | ((link: L) => string)): ForceGraphVRInstance<N, L>;
    linkWidth(width: number | ((link: L) => number)): ForceGraphVRInstance<N, L>;
    linkOpacity(opacity: number): ForceGraphVRInstance<N, L>;
    backgroundColor(color: string): ForceGraphVRInstance<N, L>;
    warmupTicks(ticks: number): ForceGraphVRInstance<N, L>;
    cooldownTicks(ticks: number): ForceGraphVRInstance<N, L>;
    _destructor?(): void;
  }

  function ForceGraphVR<N = unknown, L = unknown>(): ForceGraphVRInstance<N, L>;
  export default ForceGraphVR;
}
