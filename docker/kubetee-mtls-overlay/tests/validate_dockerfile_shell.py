#!/usr/bin/env python3
"""Validate the Dockerfile's RUN bodies against the ACTUAL runtime shell (dash).

The base image's /bin/sh is dash (Debian/Ubuntu), NOT bash. `bash -n` is
structurally blind to bash-only-isms like `set -o pipefail`, which dash rejects
with "Illegal option -o pipefail". This checks each RUN body with `dash -n`.

It also asserts the Dockerfile instruction continuations are well-formed (every
body line except the last ends with a backslash) and that no functional shell
heredoc is used mid-continuation (the buildkit parser ends the instruction at the
heredoc and treats following lines as new instructions -> "unknown instruction").
"""
import pathlib
import re
import subprocess
import sys

DF = pathlib.Path(__file__).resolve().parents[1] / "Dockerfile"
lines = DF.read_text().splitlines()

# --- 1. Extract RUN bodies, joining backslash-continuations like the shell does ---
runs = []
i = 0
while i < len(lines):
    ln = lines[i]
    if ln.startswith("RUN "):
        body = ln[4:]
        start = i + 1
        while body.rstrip().endswith("\\"):
            body = body.rstrip()[:-1] + " " + lines[i + 1].strip()
            i += 1
        runs.append((start, body))
    i += 1

# --- 2. dash -n each RUN body ---
fail = False
for start, body in runs:
    p = subprocess.run(["dash", "-n"], input=body, text=True, capture_output=True)
    status = "OK" if p.returncode == 0 else "SYNTAX ERROR"
    print(f"RUN (line {start}) dash -n {status}")
    if p.returncode != 0:
        fail = True
        print(p.stderr.strip())

# --- 3. no functional heredoc in any RUN (comment lines mentioning it are fine) ---
for n, ln in enumerate(lines, 1):
    if re.search(r"<<-?\s*['\"]?\w+", ln) and not ln.lstrip().startswith("#"):
        print(f"FAIL: functional heredoc at line {n}: {ln!r}")
        fail = True

# --- 4. bash-only-isms that dash rejects ---
for n, ln in enumerate(lines, 1):
    if "pipefail" in ln and not ln.lstrip().startswith("#"):
        print(f"FAIL: bash-only 'pipefail' at line {n}")
        fail = True

print("RESULT:", "FAILED" if fail else "ALL RUN BODIES DASH-VALID, no heredoc, no bash-only-isms")
sys.exit(1 if fail else 0)
