"""Named SQL query constants used across commands.

Scope: every constant in this module MUST be imported by at least one
caller in ``src/roam/`` or ``tests/``. The dead-query audit (2026-05-23)
removed 11 unused constants (FILE_BY_ID, FILES_BY_LANGUAGE, FILE_COUNT,
SYMBOL_BY_ID, EXPORTED_SYMBOLS, ALL_EDGES, ALL_FILE_EDGES,
CLUSTER_FOR_SYMBOL, FILE_STATS_BY_ID, COCHANGE_FOR_FILE, BLAME_FOR_FILE).
Re-add via this module only when a real caller lands — never speculatively.
"""

# File queries
FILE_BY_PATH = "SELECT * FROM files WHERE path = ?"
ALL_FILES = "SELECT * FROM files ORDER BY path"

# Symbol queries
SYMBOLS_IN_FILE = """
    SELECT s.*, f.path as file_path
    FROM symbols s JOIN files f ON s.file_id = f.id
    WHERE s.file_id = ? ORDER BY s.line_start
"""
SYMBOL_BY_NAME = """
    SELECT s.*, f.path as file_path
    FROM symbols s JOIN files f ON s.file_id = f.id
    WHERE s.name = ?
"""
SYMBOL_BY_QUALIFIED = """
    SELECT s.*, f.path as file_path
    FROM symbols s JOIN files f ON s.file_id = f.id
    WHERE s.qualified_name = ?
"""
SEARCH_SYMBOLS = """
    SELECT s.*, f.path as file_path, COALESCE(gm.pagerank, 0) as pagerank
    FROM symbols s JOIN files f ON s.file_id = f.id
    LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
    WHERE s.name LIKE ? COLLATE NOCASE
    ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name LIMIT ?
"""
TOP_SYMBOLS_BY_PAGERANK = """
    SELECT s.*, f.path as file_path, gm.pagerank
    FROM symbols s
    JOIN files f ON s.file_id = f.id
    JOIN graph_metrics gm ON s.id = gm.symbol_id
    WHERE s.kind IN ('function', 'class', 'method', 'interface')
    ORDER BY gm.pagerank DESC LIMIT ?
"""

# Edge queries
CALLERS_OF = """
    SELECT s.*, f.path as file_path, e.kind as edge_kind, e.line as edge_line
    FROM edges e
    JOIN symbols s ON e.source_id = s.id
    JOIN files f ON s.file_id = f.id
    WHERE e.target_id = ?
"""
CALLEES_OF = """
    SELECT s.*, f.path as file_path, e.kind as edge_kind, e.line as edge_line
    FROM edges e
    JOIN symbols s ON e.target_id = s.id
    JOIN files f ON s.file_id = f.id
    WHERE e.source_id = ?
"""

# File edge queries
FILE_IMPORTS = """
    SELECT f.*, SUM(fe.symbol_count) as symbol_count
    FROM file_edges fe JOIN files f ON fe.target_file_id = f.id
    WHERE fe.source_file_id = ?
    GROUP BY fe.target_file_id
"""
FILE_IMPORTED_BY = """
    SELECT f.*, SUM(fe.symbol_count) as symbol_count
    FROM file_edges fe JOIN files f ON fe.source_file_id = f.id
    WHERE fe.target_file_id = ?
    GROUP BY fe.source_file_id
"""

# Graph metrics
METRICS_FOR_SYMBOL = "SELECT * FROM graph_metrics WHERE symbol_id = ?"
TOP_BY_BETWEENNESS = """
    SELECT s.*, f.path as file_path, gm.*
    FROM graph_metrics gm
    JOIN symbols s ON gm.symbol_id = s.id
    JOIN files f ON s.file_id = f.id
    ORDER BY gm.betweenness DESC LIMIT ?
"""
TOP_BY_DEGREE = """
    SELECT s.*, f.path as file_path, gm.*
    FROM graph_metrics gm
    JOIN symbols s ON gm.symbol_id = s.id
    JOIN files f ON s.file_id = f.id
    ORDER BY (gm.in_degree + gm.out_degree) DESC LIMIT ?
"""

# Cluster queries
ALL_CLUSTERS = """
    SELECT c.cluster_id, c.cluster_label, COUNT(*) as size,
           GROUP_CONCAT(s.name, ', ') as members
    FROM clusters c JOIN symbols s ON c.symbol_id = s.id
    GROUP BY c.cluster_id ORDER BY size DESC
"""

# Git queries
# `WHERE COALESCE(file_role, 'source') = 'source'` keeps
# legacy text dumps (FoxPro extracts under docs/legacy/, build/generated
# artefacts, README/CHANGELOG churn) out of the hot list. Without this
# filter `roam weather` can rank `mhn_kin1_props.txt` highest in churn
# x complexity even though it's static reference data, not code. Mirrors
# the filter cmd_hotspots.py already applies for security hotspots.
TOP_CHURN_FILES = """
    SELECT fs.*, f.path, f.language
    FROM file_stats fs JOIN files f ON fs.file_id = f.id
    WHERE COALESCE(f.file_role, 'source') = 'source'
    ORDER BY fs.total_churn DESC LIMIT ?
"""

# Dead code.
#
# Uses NOT EXISTS rather than NOT IN to (a) avoid the classic SQL footgun
# where any NULL in the subquery makes NOT IN return zero rows -- the
# schema currently declares edges.target_id NOT NULL but a future
# migration relaxing that would silently break dead-code detection -- and
# (b) let SQLite plan a correlated index lookup against idx_edges_target
# rather than materializing the full target_id set.
UNREFERENCED_EXPORTS = """
    SELECT s.*, f.path as file_path
    FROM symbols s
    JOIN files f ON s.file_id = f.id
    WHERE s.is_exported = 1
    AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target_id = s.id)
    AND s.kind IN ('function', 'class', 'method')
    ORDER BY f.path, s.line_start
"""

# Directory / module queries
FILES_IN_DIR = "SELECT * FROM files WHERE path LIKE ? ORDER BY path"
SYMBOLS_IN_DIR = """
    SELECT s.*, f.path as file_path
    FROM symbols s JOIN files f ON s.file_id = f.id
    WHERE f.path LIKE ? AND s.is_exported = 1
    ORDER BY f.path, s.line_start
"""
