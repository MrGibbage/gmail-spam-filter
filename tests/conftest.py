import sys
from pathlib import Path

# save_corpus.py lives at the repo root (intentionally outside the spam_filter
# package — see DESIGN.md "What NOT to do"). Make it importable for tests
# regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent.parent))
