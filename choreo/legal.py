"""Operator-owned legal documents; unresolved templates never become public."""
import time
from pathlib import Path

NAMES = ("terms", "privacy", "legal")


class LegalPages:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._until = 0
        self._pages = {}

    def refresh(self):
        if time.monotonic() < self._until:
            return
        try:
            pages = {name: (self.directory / (name + ".html")).read_text() for name in NAMES}
            if any("{{" in text or not text.strip() for text in pages.values()):
                pages = {}
            if pages and 'id="report"' not in pages["legal"] and "id='report'" not in pages["legal"]:
                pages = {}
        except (OSError, UnicodeError):
            pages = {}
        self._pages = pages
        self._until = time.monotonic() + 60

    @property
    def ready(self):
        self.refresh()
        return len(self._pages) == len(NAMES)

    def get(self, name):
        self.refresh()
        return self._pages.get(name)
