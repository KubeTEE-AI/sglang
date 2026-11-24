"""Load the patched sglang tree for the overlay transport tests.

Build-time CI runs inside the base image, where /sgl-workspace/sglang/python is
on PYTHONPATH and `import sglang` already resolves to the PATCHED tree. This
conftest only matters for out-of-image runs (dev): it makes `import sglang.*`
resolve to the repo's own python/ tree.
"""
import os
import sys

_PY = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "python")
)
if os.path.isdir(_PY) and _PY not in sys.path:
    sys.path.insert(0, _PY)
