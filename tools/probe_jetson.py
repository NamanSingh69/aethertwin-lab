"""Compatibility entry point; also available as aethertwin-manage hardware."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance.hardware import main, probe

if __name__ == '__main__':
    raise SystemExit(main())
