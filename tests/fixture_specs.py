"""Point the tools at a private copy of the frozen test specs (tests/fixtures/specs), never the live specs/.

Eric edits specs/ in the Studio while tests run, so a test that read them would break on his edits (or write into
them). Import this before any hdart module, because several modules bind their spec paths at import:

    import fixture_specs  # noqa: F401  (sets HD_SPECS)
    import hdart.tools.items.item_workshop as iw

Each test process gets its own temp copy (tests may save specs), removed at exit. A test that sets HD_SPECS itself
keeps its own. Refresh the fixtures from specs/ only on purpose (and fix the tests that change).
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "specs"

if not os.environ.get("HD_SPECS"):
    _tmp = Path(tempfile.mkdtemp(prefix="hdart_specs_"))
    SPECS = _tmp / "specs"
    shutil.copytree(FIXTURES, SPECS)
    os.environ["HD_SPECS"] = str(SPECS)
    atexit.register(shutil.rmtree, _tmp, True)
else:
    SPECS = Path(os.environ["HD_SPECS"])
