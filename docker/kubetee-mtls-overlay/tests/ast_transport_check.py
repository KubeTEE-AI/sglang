"""Stdlib-only AST check of the transport-layer wiring (image build gate).

Validates the mTLS health-only + client-cert verification surface of the PATCHED
tree without importing heavy sglang modules (torch etc.): parses the four
transport files and asserts the wiring exists.
"""
import ast
import pathlib
import sys

# In-image: the editable install. In-repo (dev): the checkout's python/ tree.
_BASE = pathlib.Path("/sgl-workspace/sglang/python/sglang/srt")
if not _BASE.is_dir():
    _BASE = pathlib.Path(__file__).resolve().parents[3] / "python" / "sglang" / "srt"


def _parse(rel):
    path = _BASE / rel
    if not path.is_file():
        sys.exit(f"transport file missing: {path}")
    return ast.parse(path.read_text())


# health_check: the plaintext health-only listener (Kubelet probes in a mTLS guest)
hc = _parse("entrypoints/health_check.py")
assert any(
    isinstance(n, ast.ClassDef) and n.name == "UvicornHealthServer" for n in ast.walk(hc)
), "UvicornHealthServer missing"
hc_methods = {n.name for n in ast.walk(hc) if isinstance(n, ast.FunctionDef)}
assert {"_run", "start", "stop"} <= hc_methods, f"health_check methods missing: {hc_methods}"

# serving_hook: client-cert self-probe + ssl_client_verify validation
sh = _parse("arg_groups/serving_hook.py")
assert any(
    isinstance(n, ast.FunctionDef) and n.name == "self_check_client_cert"
    for n in ast.walk(sh)
), "self_check_client_cert missing"

# http_server: inbound verify derivation for both uvicorn and Granian paths
hs = _parse("entrypoints/http_server.py")
hs_funcs = {n.name for n in ast.walk(hs) if isinstance(n, ast.FunctionDef)}
assert {
    "_inbound_cert_reqs",
    "_inbound_client_verify",
} <= hs_funcs, f"http_server verify helpers missing: {hs_funcs}"

# serving flags
sv = _parse("arg_groups/fields/serving.py")
fields = {
    n.target.id
    for n in ast.walk(sv)
    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
}
assert {
    "ssl_client_verify",
    "health_only_port",
    "enable_ssl_refresh",
} <= fields, f"serving.py fields missing: {fields}"

print("TRANSPORT TESTS PASS (mTLS health-only + client-cert verification wired)")
