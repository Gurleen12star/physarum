import os
import tempfile

# keep real usage counters clean: tests write to a throwaway data dir
os.environ.setdefault("PHYSARUM_DATA_DIR", tempfile.mkdtemp(prefix="physarum-test-"))
