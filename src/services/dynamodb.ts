/**
 * Frontend API Client for Graph Data.
 *
 * This service handles all communication with the backend API.
 * Note: Despite the filename, this does NOT directly access DynamoDB.
 * All data flows through the Flask backend API.
 */

// ============================================================================
// Type Definitions
// ============================================================================

export interface Paper {
  title: string;
  year: number;
  document_id: string;
  tags: string[];
}

export interface Researcher {
  id: string;
  name: string;
  type: 'researcher';
  val: number;
  advisor?: string;
  contact_info?: string[];
  labs?: string[];
  standing?: string;
  papers?: Paper[];
  tags?: string[];
  influence?: number;
  about?: string;
}

export interface LabInfo {
  lab_id: string;
  description?: string;
  faculty?: string[];
}

export interface Node {
  id: string;
  name: string;
  type: 'researcher' | 'lab';
  val: number;
  [key: string]: any;
}

export interface Link {
  source: string;
  target: string;
  type: string;
}

export interface GraphData {
  nodes: Node[];
  links: Link[];
}

/** One evidence item that grounds a generated description (see backend DESCRIPTIONS.md). */
export interface DescriptionEvidence {
  id: number;
  kind: string;
  text: string;
}

/** A node's grounded `about` text + the evidence it cites (GET /api/node/description). */
export interface NodeDescription {
  id: string;
  name: string;
  kind: string;
  about: string | null;
  description_model: string | null;
  description_generated_at: string | null;
  evidence: DescriptionEvidence[];
  confidence: number | null;
}

/** A lab's enriched record beyond the 4-field graph node (GET /api/lab). */
export interface LabDetail {
  id: string;
  name: string;
  description: string | null;
  description_model: string | null;
  description_evidence: DescriptionEvidence[];
  research_areas: string[];
  pi: string | null;
  url: string | null;
  faculty: { id: string; name: string }[];
}

// ============================================================================
// API Functions
// ============================================================================

// Optional API origin override (see src/vite-env.d.ts). Default '' = relative (same-origin on
// Vercel / through the Vite dev proxy); set VITE_API_BASE_URL to target the new fly.io backend.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '';

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

/**
 * Fetches the complete graph data from the backend.
 * Returns pre-computed nodes and links for the 3D visualization.
 * Pass `runId` to view a specific repopulation run's snapshot (?run=<id>); omit for the
 * published graph (unchanged default behavior).
 */
export async function fetchGraphData(runId?: number): Promise<GraphData> {
  const query = runId != null ? `?run=${encodeURIComponent(runId)}` : '';
  const res = await fetch(apiUrl(`/api/graph/data${query}`));

  if (!res.ok) {
    throw new Error(`Failed to fetch graph data: ${res.status} ${res.statusText}`);
  }

  return await res.json();
}

/**
 * Fetches a node's grounded description + cited evidence (researcher or lab).
 * Returns null when the node has no description endpoint entry (404).
 */
export async function fetchNodeDescription(nodeId: string): Promise<NodeDescription | null> {
  const res = await fetch(apiUrl(`/api/node/description?id=${encodeURIComponent(nodeId)}`));
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to fetch node description: ${res.status} ${res.statusText}`);
  return await res.json();
}

/**
 * Fetches a lab's enriched record (description, research areas, PI, resolved faculty).
 * Returns null when the id is not a lab (404).
 */
export async function fetchLabDetail(labId: string): Promise<LabDetail | null> {
  const res = await fetch(apiUrl(`/api/lab?id=${encodeURIComponent(labId)}`));
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to fetch lab detail: ${res.status} ${res.statusText}`);
  return await res.json();
}

/**
 * Resolves a paper's document_id to its associated lab_id.
 * Used for constructing S3 paths for PDF access.
 *
 * Intentionally RELATIVE (not API_BASE): this is an AWS/Flask-backed endpoint the new FastAPI
 * service does not serve, so during a partial cutover it must stay on the Vercel/Flask origin.
 */
export async function fetchPaperLabId(documentId: string): Promise<string | null> {
  const res = await fetch('/api/graph/paper-lab-id', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_id: documentId }),
  });

  if (!res.ok) throw new Error("Failed to fetch lab_id for paper");

  const data = await res.json();
  return data.lab_id || null;
}

// ============================================================================
// Legacy Service Object (Stub)
// ============================================================================

/**
 * Legacy service object maintained for backwards compatibility.
 * New code should use the standalone functions above.
 */
export const DynamoDBService = {
  /** Stub - lab info is embedded in graph data, no separate fetch needed */
  async fetchLabInfos(_labIds: string[]): Promise<LabInfo[]> {
    return [];
  }
};
