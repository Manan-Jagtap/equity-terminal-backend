"""One-pager generation. Live path: build_onepager (legacy.py) — a ReportLab
A4 brief drawn straight from the engine's own structured data. 100% AI-free:
the Claude doc-extraction step (extract.py) was deleted with every other LLM
path on 16 Jul 2026, so no document goes to any model here anymore. peers.py
and render.py — the HTML tear-sheet stage that consumed extract's output —
remain but have no in-package feeder."""

from .legacy import build_onepager  # noqa: E402,F401
