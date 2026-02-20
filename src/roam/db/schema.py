"""SQLite schema for the Roam index."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    language TEXT,
    file_role TEXT DEFAULT 'source',
    hash TEXT,
    mtime REAL,
    line_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT,
    kind TEXT NOT NULL,
    signature TEXT,
    line_start INTEGER,
    line_end INTEGER,
    docstring TEXT,
    visibility TEXT DEFAULT 'public',
    is_exported INTEGER DEFAULT 1,
    parent_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    default_value TEXT
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    line INTEGER,
    bridge TEXT,
    confidence REAL
);

CREATE TABLE IF NOT EXISTS file_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    target_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'imports',
    symbol_count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS git_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL UNIQUE,
    author TEXT,
    timestamp INTEGER,
    message TEXT
);

CREATE TABLE IF NOT EXISTS git_file_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_id INTEGER NOT NULL REFERENCES git_commits(id) ON DELETE CASCADE,
    file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    lines_added INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS git_cochange (
    file_id_a INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    file_id_b INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    cochange_count INTEGER DEFAULT 0,
    PRIMARY KEY (file_id_a, file_id_b)
);

CREATE TABLE IF NOT EXISTS file_stats (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    commit_count INTEGER DEFAULT 0,
    total_churn INTEGER DEFAULT 0,
    distinct_authors INTEGER DEFAULT 0,
    complexity REAL DEFAULT 0,
    health_score REAL DEFAULT NULL,
    cochange_entropy REAL DEFAULT NULL,
    cognitive_load REAL DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS graph_metrics (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    pagerank REAL DEFAULT 0,
    in_degree INTEGER DEFAULT 0,
    out_degree INTEGER DEFAULT 0,
    betweenness REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clusters (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    cluster_id INTEGER NOT NULL,
    cluster_label TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_file_edges_source ON file_edges(source_file_id);
CREATE INDEX IF NOT EXISTS idx_file_edges_target ON file_edges(target_file_id);
CREATE INDEX IF NOT EXISTS idx_git_changes_file ON git_file_changes(file_id);
CREATE INDEX IF NOT EXISTS idx_git_changes_commit ON git_file_changes(commit_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_graph_metrics_pagerank ON graph_metrics(pagerank DESC);
CREATE INDEX IF NOT EXISTS idx_symbols_parent ON symbols(parent_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind_target ON edges(kind, target_id);
CREATE INDEX IF NOT EXISTS idx_file_stats_churn ON file_stats(total_churn DESC);

-- Hypergraph: n-ary commit patterns (beyond pairwise co-change)
CREATE TABLE IF NOT EXISTS git_hyperedges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_id INTEGER NOT NULL REFERENCES git_commits(id) ON DELETE CASCADE,
    file_count INTEGER NOT NULL,
    sig_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS git_hyperedge_members (
    hyperedge_id INTEGER NOT NULL REFERENCES git_hyperedges(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hyperedge_commit ON git_hyperedges(commit_id);
CREATE INDEX IF NOT EXISTS idx_hyperedge_sig ON git_hyperedges(sig_hash);
CREATE INDEX IF NOT EXISTS idx_hyperedge_members_edge ON git_hyperedge_members(hyperedge_id);
CREATE INDEX IF NOT EXISTS idx_hyperedge_members_file ON git_hyperedge_members(file_id);

-- Per-symbol complexity metrics (cognitive complexity, nesting, params)
CREATE TABLE IF NOT EXISTS symbol_metrics (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    cognitive_complexity REAL DEFAULT 0,
    nesting_depth INTEGER DEFAULT 0,
    param_count INTEGER DEFAULT 0,
    line_count INTEGER DEFAULT 0,
    return_count INTEGER DEFAULT 0,
    bool_op_count INTEGER DEFAULT 0,
    callback_depth INTEGER DEFAULT 0,
    cyclomatic_density REAL DEFAULT 0,
    halstead_volume REAL DEFAULT 0,
    halstead_difficulty REAL DEFAULT 0,
    halstead_effort REAL DEFAULT 0,
    halstead_bugs REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_symbol_metrics_complexity
    ON symbol_metrics(cognitive_complexity DESC);

-- Math signals: AST-derived signals for algorithm detection
CREATE TABLE IF NOT EXISTS math_signals (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    loop_depth INTEGER DEFAULT 0,
    has_nested_loops INTEGER DEFAULT 0,
    calls_in_loops TEXT,
    subscript_in_loops INTEGER DEFAULT 0,
    has_self_call INTEGER DEFAULT 0,
    loop_with_compare INTEGER DEFAULT 0,
    loop_with_accumulator INTEGER DEFAULT 0,
    self_call_count INTEGER DEFAULT 0,
    str_concat_in_loop INTEGER DEFAULT 0,
    loop_invariant_calls TEXT,
    loop_bound_small INTEGER DEFAULT 0
);

-- Agentic memory: persistent annotations on symbols and files
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    qualified_name TEXT,
    file_path TEXT,
    tag TEXT,
    content TEXT NOT NULL,
    author TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_annotations_symbol ON annotations(symbol_id);
CREATE INDEX IF NOT EXISTS idx_annotations_qname ON annotations(qualified_name);
CREATE INDEX IF NOT EXISTS idx_annotations_file ON annotations(file_path);
CREATE INDEX IF NOT EXISTS idx_annotations_tag ON annotations(tag);

-- Symbol effects: what functions DO (side-effect classification)
CREATE TABLE IF NOT EXISTS symbol_effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    effect_type TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'direct'
);
CREATE INDEX IF NOT EXISTS idx_symbol_effects_symbol ON symbol_effects(symbol_id);
CREATE INDEX IF NOT EXISTS idx_symbol_effects_type ON symbol_effects(effect_type);

-- Snapshots: health metrics over time
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    tag TEXT,
    source TEXT NOT NULL,
    git_branch TEXT,
    git_commit TEXT,
    files INTEGER,
    symbols INTEGER,
    edges INTEGER,
    cycles INTEGER,
    god_components INTEGER,
    bottlenecks INTEGER,
    dead_exports INTEGER,
    layer_violations INTEGER,
    health_score INTEGER,
    tangle_ratio REAL,
    avg_complexity REAL,
    brain_methods INTEGER
);

-- Runtime trace statistics: ingested from OpenTelemetry/Jaeger/Zipkin/generic traces
CREATE TABLE IF NOT EXISTS runtime_stats (
    id INTEGER PRIMARY KEY,
    symbol_id INTEGER REFERENCES symbols(id),
    symbol_name TEXT,
    file_path TEXT,
    trace_source TEXT,
    call_count INTEGER DEFAULT 0,
    p50_latency_ms REAL,
    p99_latency_ms REAL,
    error_rate REAL DEFAULT 0.0,
    last_seen TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_runtime_stats_symbol ON runtime_stats(symbol_id);
CREATE INDEX IF NOT EXISTS idx_runtime_stats_name ON runtime_stats(symbol_name);

-- Security: vulnerability tracking and reachability
CREATE TABLE IF NOT EXISTS vulnerabilities (
    id INTEGER PRIMARY KEY,
    cve_id TEXT,
    package_name TEXT NOT NULL,
    severity TEXT,
    title TEXT,
    source TEXT,
    matched_symbol_id INTEGER REFERENCES symbols(id),
    matched_file TEXT,
    reachable INTEGER DEFAULT 0,
    shortest_path TEXT,
    hop_count INTEGER,
    ingested_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_vuln_cve ON vulnerabilities(cve_id);
CREATE INDEX IF NOT EXISTS idx_vuln_package ON vulnerabilities(package_name);
CREATE INDEX IF NOT EXISTS idx_vuln_symbol ON vulnerabilities(matched_symbol_id);

-- TF-IDF vectors for semantic search
CREATE TABLE IF NOT EXISTS symbol_tfidf (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id),
    terms TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""
