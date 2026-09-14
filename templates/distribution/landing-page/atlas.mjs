import { validateAtlas, connections, radiusFor } from './atlas-model.mjs';

const NS = 'http://www.w3.org/2000/svg';
const LOAD_TIMEOUT_MS = 15000;
const mounting = new WeakSet();
let request;
function loadData() {
  if (!request) {
    const controller = new AbortController();
    let timer;
    const deadline = new Promise((resolve, reject) => {
      timer = setTimeout(() => {
        reject(new Error('Map request timed out'));
        controller.abort();
      }, LOAD_TIMEOUT_MS);
    });
    // Cover both headers and JSON body. The race also settles if a fetch/body
    // implementation ignores cancellation; its late result cannot mount a map.
    const data = Promise.resolve().then(() => fetch('/atlas-data.json', { signal: controller.signal })).then(response => {
      if (!response.ok) throw new Error('Map request failed');
      return response.json();
    }).then(validateAtlas);
    request = Promise.race([data, deadline]).catch(error => {
      controller.abort(); request = null; throw error;
    }).finally(() => clearTimeout(timer));
  }
  return request;
}
function svgElement(tag, attrs = {}, text) {
  const element = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text;
  return element;
}
function setText(root, selector, value) {
  const element = root.querySelector(selector);
  if (element) element.textContent = value;
}

async function mount(root, index) {
  if (mounting.has(root)) return;
  mounting.add(root);
  const retry = root.querySelector('[data-retry]');
  try {
    // Keep a pending retry focusable; the guard prevents concurrent remounts.
    if (retry) retry.setAttribute('aria-disabled', 'true');
    setText(root, '[data-load-status]', 'Loading the interactive map. You can still read the static map.');
    const data = await loadData();
    let selected = data.nodes.some(node => node.id === 'commands') ? 'commands' : data.nodes[0].id;
    let mode = 'all';
    const compact = root.hasAttribute('data-compact');
    const byId = new Map(data.nodes.map(node => [node.id, node]));
    const graph = svgElement('svg', { viewBox:'0 0 960 620', role:'group', 'aria-label':'Code areas. Select one to inspect its import connections.' });
    const defs = svgElement('defs');
    const markerId = `atlas-arrow-${index}`;
    const marker = svgElement('marker', { id:markerId, viewBox:'0 0 10 10', refX:9, refY:5, markerWidth:5, markerHeight:5, orient:'auto-start-reverse' });
    marker.append(svgElement('path', { d:'M 0 0 L 10 5 L 0 10 z', class:'atlas-arrow' }));
    defs.append(marker); graph.append(defs);
    const edgeElements = data.edges.map(edge => {
      const a = byId.get(edge.source), b = byId.get(edge.target);
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy);
      const start = radiusFor(a) + 3, end = radiusFor(b) + 5;
      const element = svgElement('path', { d:`M ${a.x + dx / distance * start} ${a.y + dy / distance * start} L ${b.x - dx / distance * end} ${b.y - dy / distance * end}`, class:'atlas-edge', 'aria-hidden':'true' });
      graph.append(element);
      return { edge, element };
    });
    const nodeElements = data.nodes.map(node => {
      const radius = radiusFor(node);
      const anchor = node.x < 150 ? 'start' : node.x > 810 ? 'end' : 'middle';
      const element = svgElement('g', { class:'atlas-node', transform:`translate(${node.x} ${node.y})`, role:'button', tabindex:'0', 'aria-label':`${node.label}, ${node.files} Python files`, 'aria-pressed':'false' });
      element.append(svgElement('circle', { r:radius + 9, class:'atlas-node-ring' }), svgElement('circle', { r:radius, class:'atlas-node-disc' }), svgElement('circle', { r:4, class:'atlas-node-core' }), svgElement('text', { y:radius + 27, 'text-anchor':anchor }, node.label), svgElement('text', { y:radius + 45, class:'atlas-node-count', 'text-anchor':anchor }, `${node.files} files`));
      element.addEventListener('click', () => { selected = node.id; render(); });
      element.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selected = node.id; render(); }
      });
      graph.append(element);
      return { node, element };
    });
    const select = root.querySelector('[data-node-select]');
    if (select) {
      select.replaceChildren(...data.nodes.map(node => {
        const option = document.createElement('option'); option.value = node.id; option.textContent = node.label; return option;
      }));
      select.addEventListener('change', () => {
        selected = select.value; render();
        if (!compact) {
          // Move only the map's horizontal viewport, never the reader's page.
          const pane = graph.parentElement;
          pane.scrollLeft = byId.get(selected).x * graph.getBoundingClientRect().width / 960 - pane.clientWidth / 2;
        }
      });
    }
    root.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => { mode = button.dataset.mode; render(); }));
    function render() {
      const current = byId.get(selected);
      const active = connections(data, selected, mode);
      nodeElements.forEach(({ node, element }) => {
        element.classList.toggle('is-selected', node.id === selected);
        element.classList.toggle('is-connected', node.id !== selected && active.visible.has(node.id));
        element.classList.toggle('is-dimmed', mode !== 'all' && !active.visible.has(node.id));
        element.setAttribute('aria-pressed', String(node.id === selected));
      });
      edgeElements.forEach(({ edge, element }) => {
        const on = active.edges.includes(edge);
        element.classList.toggle('is-active', on);
        element.style.opacity = on ? '' : mode === 'all' ? '.09' : '.025';
        if (on && !compact) element.setAttribute('marker-end', `url(#${markerId})`);
        else element.removeAttribute('marker-end');
      });
      if (select) select.value = selected;
      root.querySelectorAll('[data-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
      setText(root, '[data-selection-title]', compact ? current.label : current.title);
      setText(root, '[data-selection-copy]', current.description);
      setText(root, '[data-map-count]', `${data.nodes.length} areas · ${data.files_scanned.toLocaleString()} files`);
      setText(root, '[data-map-hint]', compact ? 'Choose an area, or select a dot. See what connects.' : mode === 'all' ? 'Select an area to follow its connections. Larger circles contain more Python files.' : mode === 'incoming' ? `Highlighted areas import modules in ${current.label}. These are investigation leads, not runtime impact predictions.` : `${current.label} imports modules in the highlighted areas. Arrows point toward the dependency.`);
      const stats = root.querySelector('[data-detail-stats]');
      if (stats) {
        stats.replaceChildren(...[[current.files, 'Python files'], [active.visible.size - 1, mode === 'incoming' ? 'importing areas' : mode === 'outgoing' ? 'dependency areas' : 'connected areas']].map(([value, label]) => {
          const div = document.createElement('div'), strong = document.createElement('strong'), span = document.createElement('span');
          strong.textContent = value; span.textContent = label; div.append(strong, span); return div;
        }));
      }
      const files = root.querySelector('[data-files]');
      if (files) files.replaceChildren(...current.examples.map(path => { const li = document.createElement('li'); li.textContent = path; return li; }));
      const relationships = !compact && root.querySelector('[data-relationships]');
      if (relationships) {
        // The drawn edges are decorative to assistive technology. Keep their
        // directional names readable outside the concise live announcement.
        const directions = [['outgoing', 'Imports from'], ['incoming', 'Imported by']];
        relationships.replaceChildren(...directions.filter(([direction]) => mode === 'all' || mode === direction).map(([direction, title]) => {
          const outgoing = direction === 'outgoing';
          const neighbors = active.edges.filter(edge => (outgoing ? edge.source : edge.target) === selected)
            .map(edge => byId.get(outgoing ? edge.target : edge.source));
          const group = document.createElement('div'), heading = document.createElement('h4');
          heading.textContent = title; group.append(heading);
          if (neighbors.length) {
            const list = document.createElement('ul');
            list.className = 'atlas-relationship-list';
            list.replaceChildren(...neighbors.map(node => {
              const item = document.createElement('li'); item.textContent = node.label; return item;
            }));
            group.append(list);
          } else {
            const empty = document.createElement('p');
            empty.className = 'atlas-relationship-empty';
            empty.textContent = outgoing ? 'No outgoing connections to other areas in this snapshot.'
              : 'No incoming connections from other areas in this snapshot.';
            group.append(empty);
          }
          return group;
        }));
      }
    }
    root.querySelector('[data-graph]').replaceChildren(graph);
    root.querySelectorAll('[data-controls]').forEach(element => { element.hidden = false; });
    setText(root, '[data-load-status]', 'Interactive map ready. Choose an area to explore.');
    const provenance = document.querySelector('[data-provenance]');
    if (provenance) provenance.textContent = `${data.files_scanned} Python files scanned; ${data.module_connections} resolved module-import connections; ${data.unresolved_local_imports} unresolved local import locations. Source SHA-256: ${data.source_sha256}. ${data.definition}`;
    render();
    if (retry) {
      // Move focus only when hiding the control the reader is still using.
      if (document.activeElement === retry && select) select.focus({ preventScroll: true });
      retry.hidden = true; retry.onclick = null;
    }
  } catch {
    setText(root, '[data-load-status]', 'The interactive data could not be loaded. The static map is still available; its snapshot may differ from newer source.');
    if (retry) { retry.hidden = false; retry.onclick = () => mount(root, index); }
  } finally {
    mounting.delete(root);
    if (retry) retry.removeAttribute('aria-disabled');
  }
}
document.querySelectorAll('[data-atlas]').forEach((root, index) => mount(root, index));
