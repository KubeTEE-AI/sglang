#!/usr/bin/env python3
"""Validate staged transport-canary build context without importing SGLang."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise ValueError("exactly one context directory is required")
context = Path(sys.argv[1])
sources = []
for line in (context / "Dockerfile").read_text().splitlines():
    if not line.startswith("COPY "):
        continue
    fields = line.split()
    assert len(fields) >= 3, line
    assert not any(f.startswith("--") for f in fields[1:-1]), line
    for source in fields[1:-1]:
        assert source and not Path(source).is_absolute(), source
        assert ".." not in Path(source).parts, source
        assert (context / source).exists(), f"Missing staged Docker COPY source: {source}"
        sources.append(source)
assert sources == [
    "merged.patch",
    "changed_py.txt",
    "k31-image",
    "scripts-transport-launcher.py",
    "transport-test.py",
], sources
assert (context / "k31-image" / "verify_image.py").is_file()
assert (context / "k31-image" / "canary-test-requirements.txt").is_file()
print("All staged Docker COPY sources and verification assets resolve.")
