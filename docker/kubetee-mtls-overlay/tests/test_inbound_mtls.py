"""Prove --ssl-ca-certs actually demands a client certificate (patch 0005).

Starts a real uvicorn listener configured the way SGLang configures it, then
asserts:
  * CA bundle set    -> a client with NO cert is refused (mTLS enforced)
  * CA bundle set    -> a client signed by that CA is accepted
  * CA bundle absent -> a client with no cert still connects (plain TLS,
                        unchanged legacy behaviour)

Fails on stock SGLang: it passes ssl_ca_certs but leaves verify_mode
CERT_NONE, so the no-cert client connects in every case.
"""

import os
import socket
import ssl
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import pytest


def _gen_certs(workdir: Path):
    """Self-signed CA + server leaf + client leaf (openssl, no deps)."""

    def run(*args):
        subprocess.run(args, check=True, capture_output=True)

    ca_key = workdir / "ca.key"
    ca_crt = workdir / "ca.crt"
    ca_ext = workdir / "ca.ext"
    ca_ext.write_text(
        "basicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,keyCertSign,cRLSign\n"
        "subjectKeyIdentifier=hash\n"
    )
    run(
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_crt), "-days", "1",
        "-subj", "/CN=test-ca", "-addext",
        "basicConstraints=critical,CA:TRUE", "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )

    def leaf(name, cn, ext):
        key = workdir / f"{name}.key"
        csr = workdir / f"{name}.csr"
        crt = workdir / f"{name}.crt"
        extfile = workdir / f"{name}.ext"
        extfile.write_text(ext)
        run(
            "openssl", "req", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(csr), "-subj", f"/CN={cn}",
        )
        run(
            "openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_crt),
            "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(crt),
            "-days", "1", "-extfile", str(extfile),
        )
        return str(crt), str(key)

    server = leaf(
        "server", "127.0.0.1",
        "subjectAltName=IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth\n"
        "authorityKeyIdentifier=keyid,issuer\n",
    )
    client = leaf(
        "client", "test-client",
        "extendedKeyUsage=clientAuth\n"
        "authorityKeyIdentifier=keyid,issuer\n",
    )
    return dict(ca=str(ca_crt), server=server, client=client)


APP_MODULE = textwrap.dedent(
    """
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
    """
)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(cert, key, ca, cert_reqs):
    import uvicorn

    cfg = uvicorn.Config(
        "mtls_probe:app",
        host="127.0.0.1",
        port=_free_port(),
        log_level="error",
        ssl_certfile=cert,
        ssl_keyfile=key,
        ssl_ca_certs=ca,
        ssl_cert_reqs=cert_reqs,
    )
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return server, cfg.port


def _connect(port, client=None, ca=None):
    """Full request/response.

    TLS 1.3 surfaces a missing client certificate as an alert AFTER the
    handshake, on the first read/write, so the handshake alone proves nothing
    -- we must actually exchange bytes. Returns "connected" only if a real 200
    response came back; "rejected" for any refusal (TLS alert, drop, timeout).
    """
    ctx = ssl.create_default_context(cafile=ca)
    if client:
        ctx.load_cert_chain(*client)
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError:
        return "rejected"
    try:
        tls = ctx.wrap_socket(s, server_hostname="127.0.0.1")
    except (ssl.SSLError, OSError):
        return "rejected"
    try:
        tls.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        data = tls.recv(1024)
        return "connected" if b"200" in data else "rejected"
    except (ssl.SSLError, OSError):
        return "rejected"
    finally:
        try:
            tls.close()
        except OSError:
            pass


def _http_server_src() -> Path:
    """Locate ``http_server.py`` in EITHER test layout.

    Worktree: ``<repo>/docker/kubetee-mimo-overlay/tests/this.py`` -> walk up to
    ``<repo>/python/sglang/srt/entrypoints/http_server.py``. Image: the test is
    copied to ``/tmp/mimo-tests/`` (only 2 ancestors — a hardcoded
    ``parents[3]`` raises IndexError there, which is what failed the
    2026-11-24 build) with the patched editable install at
    ``/sgl-workspace/sglang``. ``SGLANG_HTTP_SERVER_SRC`` overrides both.
    """
    env = os.environ.get("SGLANG_HTTP_SERVER_SRC")
    if env:
        return Path(env)
    rel = Path("python/sglang/srt/entrypoints/http_server.py")
    here = Path(__file__).resolve()
    for root in (here.parent, *here.parents):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    candidate = Path("/sgl-workspace/sglang") / rel
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"cannot locate {rel} from {here}")


def _derive_cert_reqs(ca):
    """Route through SGLang's own helper (patch 0005) rather than guessing.

    On stock SGLang this raises ``AttributeError`` because the helpers do not
    exist -- which is exactly the point: the test then fails against stock, and
    passes only when 0005 is applied.
    """
    import ast
    from types import SimpleNamespace

    src = _http_server_src()
    tree = ast.parse(src.read_text())
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name in ("_inbound_client_verify", "_inbound_cert_reqs")
    ]
    assert len(nodes) == 2, "patch 0005 missing: inbound mTLS helpers not found"
    ns = {"ssl": ssl}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<helpers>", "exec"), ns)
    return ns["_inbound_cert_reqs"](SimpleNamespace(ssl_ca_certs=ca))


def test_mtls_enforced_and_legacy_path_unchanged(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "mtls_probe.py").write_text(APP_MODULE)

    c = _gen_certs(tmp_path)
    cert, key = c["server"]

    # --- mTLS ENFORCED: a client with NO certificate must be refused.
    # cert_reqs comes from SGLang's helper, so this exercises the real wiring.
    server, port = _serve(cert, key, c["ca"], _derive_cert_reqs(c["ca"]))
    try:
        assert _connect(port, client=None, ca=c["ca"]) == "rejected", (
            "patch 0005 FAILS: server accepted a client with NO certificate "
            "even with --ssl-ca-certs set (inbound verify_mode is CERT_NONE)"
        )
        assert _connect(port, client=c["client"], ca=c["ca"]) == "connected"
    finally:
        server.should_exit = True

    # --- LEGACY PATH UNCHANGED: no CA bundle -> plain TLS, no client cert.
    server2, port2 = _serve(cert, key, None, _derive_cert_reqs(None))
    try:
        assert _connect(port2, client=None, ca=c["ca"]) == "connected"
    finally:
        server2.should_exit = True


def test_sglang_derives_cert_reqs_from_ca_bundle():
    """The helper must flip only on the presence of a CA bundle.

    ``http_server`` pulls in torch via the platform stubs, so the two helpers
    are extracted from source and exec'd rather than imported.
    """
    import ast
    from types import SimpleNamespace

    src = _http_server_src().read_text()
    tree = ast.parse(src)
    wanted = {"_inbound_client_verify", "_inbound_cert_reqs"}
    nodes = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in wanted
    ]
    assert len(nodes) == 2, f"expected both helpers, found {[n.name for n in nodes]}"
    ns = {"ssl": ssl}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<helpers>", "exec"), ns)
    _inbound_client_verify = ns["_inbound_client_verify"]
    _inbound_cert_reqs = ns["_inbound_cert_reqs"]

    with_ca = SimpleNamespace(ssl_ca_certs="/etc/tls/ca.crt")
    without_ca = SimpleNamespace(ssl_ca_certs=None)
    assert _inbound_client_verify(with_ca) is True
    assert _inbound_client_verify(without_ca) is False
    assert _inbound_cert_reqs(with_ca) == ssl.CERT_REQUIRED
    assert _inbound_cert_reqs(without_ca) == ssl.CERT_NONE
