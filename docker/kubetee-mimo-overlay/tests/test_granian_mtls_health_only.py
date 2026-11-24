# SPDX-License-Identifier: Apache-2.0
"""Real Granian/Uvicorn test of KubeTEE's external transport boundary.

No model or GPUs are loaded; health uses the original handler attached to a
minimal manager stub. CI generates single-use test identities in the runner and
this entrypoint deletes their entire directory after the checks finish.
"""

import multiprocessing as mp
import os
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path

import h2.config
import h2.connection
import h2.events
import requests

from sglang.test.ci.ci_register import register_cpu_ci

register_cpu_ci(est_time=60, suite="base-a-test-cpu")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def openssl(*args):
    subprocess.run(("openssl", *map(str, args)), check=True, capture_output=True)


def identities(cert_dir):
    cert_dir = Path(cert_dir)
    cert_dir.mkdir(parents=True, exist_ok=False)
    for name in ("ca", "wrong-ca"):
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={name}.canary-ci.invalid",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            cert_dir / f"{name}.key",
            "-out",
            cert_dir / f"{name}.pem",
        )
    for name, ca, usage in (
        ("server", "ca", "serverAuth"),
        ("client", "ca", "clientAuth"),
        ("wrong", "wrong-ca", "clientAuth"),
    ):
        openssl(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            cert_dir / f"{name}.key",
            "-out",
            cert_dir / f"{name}.csr",
            "-subj",
            f"/CN={name}.canary-ci.invalid",
        )
        # OpenSSL 4.x rejects -addext authorityKeyIdentifier=keyid; use an extfile.
        ext = cert_dir / f"{name}.ext"
        lines = [
            f"extendedKeyUsage={usage}",
            "subjectKeyIdentifier=hash",
            "authorityKeyIdentifier=keyid,issuer",
        ]
        if name == "server":
            lines += ["basicConstraints=critical,CA:FALSE", "subjectAltName=IP:127.0.0.1"]
        ext.write_text("\n".join(lines) + "\n")
        openssl(
            "x509",
            "-req",
            "-in",
            cert_dir / f"{name}.csr",
            "-CA",
            cert_dir / f"{ca}.pem",
            "-CAkey",
            cert_dir / f"{ca}.key",
            "-CAcreateserial",
            "-days",
            "1",
            "-out",
            cert_dir / f"{name}.crt",
            "-extfile",
            str(ext),
        )
    # KBS serves a traditional PKCS#1 RSA key; do not convert to PKCS#8.
    key_header = (cert_dir / "server.key").read_text().splitlines()[0]
    # OpenSSL 3 emits PKCS#1 here; OpenSSL 4 emits PKCS#8. Both are loadable.
    assert key_header in ("-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----"), key_header


def http2_stream(port, context):
    connection = h2.connection.H2Connection(
        config=h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
    )
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        raw.settimeout(10)
        with context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            assert tls.selected_alpn_protocol() == "h2", "HTTP/2 not negotiated"
            connection.initiate_connection()
            tls.sendall(connection.data_to_send())
            stream = connection.get_next_available_stream_id()
            connection.send_headers(
                stream,
                [
                    (":method", "POST"),
                    (":scheme", "https"),
                    (":authority", "127.0.0.1"),
                    (":path", "/stream-test"),
                    ("content-length", "0"),
                ],
                end_stream=True,
            )
            data = b""
            while not data.endswith(b"data: [DONE]\n\n"):
                incoming = tls.recv(65535)
                if not incoming:
                    break
                for event in connection.receive_data(incoming):
                    if isinstance(event, h2.events.DataReceived):
                        data += event.data
                        connection.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                tls.sendall(connection.data_to_send())
            assert data == b"data: first\n\ndata: second\n\ndata: [DONE]\n\n", data
            return b"data: first"


def wait_port(port, process, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not process.is_alive():
            raise RuntimeError(f"launcher exited {process.exitcode} before {port}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise TimeoutError(f"no server on {port}")


def _launcher_path():
    """Locate the transport launcher in the repo checkout or the image.

    In the checkout it lives at <repo>/scripts/... (this file is at
    <repo>/docker/kubetee-mimo-overlay/tests/...). In the image it is copied
    alongside the patches. SGLANG_TRANSPORT_LAUNCHER overrides both.
    """
    env = os.environ.get("SGLANG_TRANSPORT_LAUNCHER")
    if env:
        return env
    rel = "scripts/kimi-k31-granian-transport-test.py"
    here = Path(__file__).resolve()
    # Walk up (no hardcoded parents[N] — the image copies tests to a shallower
    # path and parents[N] then raises IndexError; same lesson as
    # test_inbound_mtls._http_server_src, 2026-11-24).
    for root in (here.parent, *here.parents):
        candidate = root / rel
        if candidate.is_file():
            return str(candidate)
    candidate = Path("/tmp/mimo-tests") / Path(rel).name
    if candidate.is_file():
        return str(candidate)
    raise FileNotFoundError(f"cannot locate {rel} from {here}")


def serve(https, health):
    """Real model-independent launcher fixture via the real SGLang CLI."""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "kimi_k31_granian_transport_test",
        _launcher_path(),
    )
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    sys.argv = [launcher.__file__, str(https), str(health)]
    launcher.main()


def main():
    cert_dir = os.environ.get("SGLANG_TEST_CERTS")
    if not cert_dir or cert_dir == "/":
        raise ValueError("SGLANG_TEST_CERTS must be a dedicated CI directory")
    try:
        identities(cert_dir)
        https, health = free_port(), free_port()
        assert https != health
        proc = mp.get_context("spawn").Process(target=serve, args=(https, health))
        proc.start()
        results = {}
        try:
            wait_port(https, proc)
            wait_port(health, proc)
            context = ssl.create_default_context(cafile=f"{cert_dir}/ca.pem")
            context.set_alpn_protocols(["h2"])
            context.load_cert_chain(f"{cert_dir}/client.crt", f"{cert_dir}/client.key")
            results["SSE_H2"] = http2_stream(https, context).decode()

            # TLS verification stays enabled. CI's server has an explicit loopback SAN.
            session = requests.Session()
            session.cert = (f"{cert_dir}/client.crt", f"{cert_dir}/client.key")
            session.verify = f"{cert_dir}/ca.pem"
            response = session.post(
                f"https://127.0.0.1:{https}/generate-test", timeout=10
            )
            assert response.status_code == 200 and response.json()["text"] == "canary"
            results["INFERENCE_MTLS"] = response.status_code

            for path in ("/", "/v1/models", "/v1/attestation", "/v1/chat/completions"):
                response = requests.get(f"http://127.0.0.1:{health}{path}", timeout=10)
                assert response.status_code == 404, (path, response.status_code)
            results["NO_INFERENCE_PLAINTEXT"] = 404
            for method, path, expected in (
                ("get", "/health", 200),
                ("get", "/health_generate", 200),
                ("get", "//health_generate", 404),
                ("head", "/health", 404),
                ("post", "/health", 404),
                ("post", "/generate", 404),
                ("post", "/flush_cache", 404),
            ):
                response = getattr(requests, method)(
                    f"http://127.0.0.1:{health}{path}", timeout=25
                )
                assert response.status_code == expected, (
                    method,
                    path,
                    response.status_code,
                )
            results["HEALTH_ONLY_PATHS"] = "passed"

            for name, cert in (
                ("MISSING_CLIENT_CERT_REJECTED", None),
                (
                    "UNTRUSTED_CA_REJECTED",
                    (f"{cert_dir}/wrong.crt", f"{cert_dir}/wrong.key"),
                ),
            ):
                try:
                    requests.get(
                        f"https://127.0.0.1:{https}/health_generate",
                        verify=f"{cert_dir}/ca.pem",
                        cert=cert,
                        timeout=10,
                    )
                    raise AssertionError(f"Broken client certificate accepted: {name}")
                except requests.exceptions.SSLError:
                    results[name] = "yes"

            # Auxiliary listener failure must stop the primary transport.
            # This fixture intentionally exits the health event loop; no sleeps
            # are used to pretend health liveness independently of Granian.
            proc.terminate()
            proc.join(30)
            if proc.is_alive():
                raise RuntimeError("launcher did not terminate within 30s")
            results["CLEAN_SHUTDOWN"] = proc.exitcode
            proc = None
            print("TRANSPORT_PASSED", results, flush=True)
        finally:
            if proc is not None:
                proc.terminate()
                proc.join(30)
                if proc.is_alive():
                    raise RuntimeError("launcher did not terminate after test failure")
    finally:
        if os.environ.pop("SGLANG_TEST_KEEP_KEYS", "") != "1":
            shutil.rmtree(cert_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
