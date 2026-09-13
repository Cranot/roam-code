// The website lens is literal module imports, not runtime change impact.
const nonemptyText = value => typeof value === 'string' && value.trim().length > 0;

export function validateAtlas(data) {
  if (!data || data.schema_version !== 1 || !Array.isArray(data.nodes) || !data.nodes.length || !Array.isArray(data.edges)) throw new Error('Map data is unavailable');
  const nodes = new Map();
  for (const node of data.nodes) {
    if (!node || ![node.id, node.label, node.title, node.description].every(nonemptyText) || nodes.has(node.id)
      || !Number.isFinite(node.x) || node.x < 0 || node.x > 960
      || !Number.isFinite(node.y) || node.y < 0 || node.y > 620
      || !Number.isSafeInteger(node.files) || node.files < 1
      || !Array.isArray(node.examples) || !node.examples.every(nonemptyText)) throw new Error('Invalid map area');
    nodes.set(node.id, node);
  }
  const pairs = new Set();
  let displayedConnections = 0;
  for (const edge of data.edges) {
    if (!edge || !nodes.has(edge.source) || !nodes.has(edge.target) || edge.source === edge.target
      || !Number.isSafeInteger(edge.count) || edge.count < 1) throw new Error('Invalid map connection');
    const pair = JSON.stringify([edge.source, edge.target]);
    const a = nodes.get(edge.source), b = nodes.get(edge.target);
    // The renderer divides by this distance. A duplicate pair also inflates evidence.
    if (pairs.has(pair) || Math.hypot(b.x - a.x, b.y - a.y) === 0) throw new Error('Invalid map connection');
    pairs.add(pair);
    displayedConnections += edge.count;
  }
  // Within-area imports count in the total but are intentionally absent from edges.
  if (!Number.isSafeInteger(data.files_scanned) || data.files_scanned !== data.nodes.reduce((sum, node) => sum + node.files, 0)
    || !Number.isSafeInteger(data.module_connections) || data.module_connections < displayedConnections
    || !Number.isSafeInteger(data.unresolved_local_imports) || data.unresolved_local_imports < 0
    || !Number.isSafeInteger(displayedConnections) || !nonemptyText(data.definition)
    || typeof data.source_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(data.source_sha256)) throw new Error('Invalid map provenance');
  return data;
}

export function connections(data, selected, mode = 'all') {
  if (!['all', 'incoming', 'outgoing'].includes(mode) || !data.nodes.some(node => node.id === selected)) throw new Error('Unknown map selection');
  const edges = data.edges.filter(edge => mode === 'outgoing' ? edge.source === selected : mode === 'incoming' ? edge.target === selected : edge.source === selected || edge.target === selected);
  return { edges, visible: new Set([selected, ...edges.flatMap(edge => [edge.source, edge.target])]) };
}

export const radiusFor = node => 13 + Math.sqrt(node.files) * 1.4;
