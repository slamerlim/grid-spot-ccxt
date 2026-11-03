"""Expose the same offline interface as the gridctl entry point."""

from grid.cli import main

raise SystemExit(main())
