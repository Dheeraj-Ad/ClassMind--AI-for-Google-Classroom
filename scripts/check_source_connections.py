"""Read public sample sources and print only availability, never credentials."""
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.classroom.load_sources import load_source
from backend.classroom.source_types import link_source
from backend.utils.errors import ServiceError

output = ROOT / ".test-runs" / ("sources-" + uuid.uuid4().hex[:8])
for url in ("https://www.youtube.com/watch?v=aircAruvnKk", "https://example.com"):
    item = link_source(url)
    try:
        documents, warnings = load_source(item, output, {"source_url": item["source_url"]})
        print(f"{item['source_type']}: imported {len(documents)} passages; {len(warnings)} warnings", flush=True)
    except ServiceError as error:
        print(f"{item['source_type']}: {error.message}", flush=True)
