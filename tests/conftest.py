import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CHORES_NO_BACKUP"] = "1"
os.environ["PARENT_PIN"] = "4321"
import tempfile

os.environ["CHORES_DATA"] = tempfile.mkdtemp(prefix="chores-test-")
