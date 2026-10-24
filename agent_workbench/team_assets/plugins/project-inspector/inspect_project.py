"""A sample, read-only declarative plugin command."""
import json
import sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]).resolve()
counts = Counter(p.suffix for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".js", ".html", ".md"})
print(json.dumps(dict(counts)))
