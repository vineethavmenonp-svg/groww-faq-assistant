"""CLI: python -m src.ingest [load|chunk|embed|index].

'load' scrapes allowlisted pages into artifacts/raw/, 'chunk' turns them into
artifacts/chunks/chunks.jsonl, 'embed' writes MiniLM vectors into
artifacts/embed/, 'index' replaces the Chroma collection at data/chroma/.
"""

from __future__ import annotations

import sys

STAGES = ("load", "chunk", "embed", "index")


def run_load() -> int:
    # Imported here so the CLI stays import-light.
    from src.ingest.load import load_allowlist

    load_allowlist()
    return 0


def run_chunk() -> int:
    from src.ingest.chunk import chunk_pages

    chunk_pages()
    return 0


def run_embed() -> int:
    from src.ingest.embed import embed_chunks

    embed_chunks()
    return 0


def run_index() -> int:
    from src.ingest.index import index_chunks

    index_chunks()
    return 0


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage != "all" and stage not in STAGES:
        print(f"Unknown ingest stage: {stage!r}. Use one of {', '.join(STAGES)} or 'all'.")
        sys.exit(2)

    stages = list(STAGES) if stage == "all" else [stage]
    for name in stages:
        if name == "load":
            run_load()
        elif name == "chunk":
            run_chunk()
        elif name == "embed":
            run_embed()
        elif name == "index":
            run_index()
        else:
            print(f"Ingest '{name}' is not implemented yet (later implementation step).")


if __name__ == "__main__":
    main()
