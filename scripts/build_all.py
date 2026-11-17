"""Run the local KB + Q&A build end to end. Sends nothing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_qna import build as build_qna  # noqa: E402
from build_readme import build_readme  # noqa: E402
from common import ensure_dirs  # noqa: E402
from fetch_abstracts import fetch_abstracts  # noqa: E402
from fetch_pages import fetch_all  # noqa: E402
from parse_kb import chunk_all, parse_all  # noqa: E402


def main() -> None:
    ensure_dirs()
    force = "--force" in sys.argv
    skip_fetch = "--skip-fetch" in sys.argv
    if not skip_fetch:
        fetch_all(force=force)
    records = parse_all()
    fetch_abstracts(records)
    # Re-read and re-chunk so paper abstracts are included
    chunk_all()
    build_qna()
    build_readme()
    print("Build complete.", flush=True)


if __name__ == "__main__":
    main()
