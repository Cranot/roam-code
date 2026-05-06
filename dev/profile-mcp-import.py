"""Profile MCP server cold import to find slow modules."""
import cProfile
import io
import pstats

pr = cProfile.Profile()
pr.enable()
import roam.mcp_server  # noqa: E402,F401

pr.disable()
ps = pstats.Stats(pr).sort_stats("cumulative")
buf = io.StringIO()
ps.stream = buf
ps.print_stats(80)
sep = "\\"
for line in buf.getvalue().splitlines():
    if ".py:" not in line:
        continue
    parts = line.split()
    try:
        cum = float(parts[3])
    except (ValueError, IndexError):
        continue
    if cum > 0.15:
        tail = parts[-1].replace(sep, "/").split("/")[-1]
        print(f"{cum:5.2f}s {tail[:80]}")
