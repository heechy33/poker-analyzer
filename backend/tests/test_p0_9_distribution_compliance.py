"""Release-input guard for Phase 0.9."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
AUDIT = ROOT / "backend" / "scripts" / "audit_p0_9_distribution.py"


def test_distribution_source_audit_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(AUDIT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "P0.9 distribution/source audit passed"
