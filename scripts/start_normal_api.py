"""Normal startup deliberately ignores inherited disposable-lab settings."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ('DEVMESH_DATABASE', 'DEVMESH_EXECUTION_REPOSITORY', 'DEVMESH_TEST_COMMAND'):
    os.environ.pop(name, None)
from backend.main import create_app
import uvicorn
if __name__ == '__main__':
    uvicorn.run(create_app(), host='127.0.0.1', port=8420, workers=1, access_log=False)
