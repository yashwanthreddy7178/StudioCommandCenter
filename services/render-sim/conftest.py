import sys
from pathlib import Path

# Add service directory and workspace root to sys.path
service_dir = Path(__file__).resolve().parent
workspace_root = service_dir.parent.parent

if str(service_dir) not in sys.path:
    sys.path.insert(0, str(service_dir))
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))
