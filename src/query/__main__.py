"""CLI: python -m src.query [subcommand-or-question].

`python -m src.query.pipeline --q "..."` is the documented entry; this module
forwards plain invocations to the same pipeline.
"""

from __future__ import annotations

import sys

from src.query.pipeline import main


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
