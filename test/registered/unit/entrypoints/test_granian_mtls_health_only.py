# SPDX-License-Identifier: Apache-2.0
"""Real Granian/Uvicorn test of KubeTEE's external transport boundary.

No model or GPUs are loaded; health uses the original handler attached to a
minimal manager stub. CI generates single-use test identities in the runner and
this entrypoint deletes their entire directory after the checks finish.
"""

import http.client
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

from sglang.test.ci.ci_register import register_cpu_ci

register_cpu_ci(est_time=60, suite="base-a-test-cpu")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def openssl(*args):
    try:
        subprocess.run(("openssl", *map(str, args)), check=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        print(error.stderr.decode(), end="", flush=True)
        raise


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
        args = [
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
            cert_dir / f"{name}.extensions",
            "-extensions",
            "identity",
        ]
        extensions = [
            f"extendedKeyUsage={usage}",
            "subjectKeyIdentifier=hash",
            "authorityKeyIdentifier=keyid",
        ]
        if name == "server":
            extensions += [
                "basicConstraints=critical,CA:FALSE",
                "subjectAltName=IP:127.0.0.1",
            ]
        (cert_dir / f"{name}.extensions").write_text(
            "[identity]\n" + "\n".join(extensions) + "\n"
        )
        openssl(*args)
    # Exercise the same PKCS#1 serialization as KBS, even with OpenSSL 3/4
    # where req/genrsa may emit PKCS#8 by default. The RSA key is unchanged.
    openssl(
        "rsa",
        "-in",
        cert_dir / "server.key",
        "-traditional",
        "-out",
        cert_dir / "server-pkcs1.key",
    )
    (cert_dir / "server.key").unlink()
    (cert_dir / "server-pkcs1.key").rename(cert_dir / "server.key")
    key_header = (cert_dir / "server.key").read_text().splitlines()[0]
    assert key_header == "-----BEGIN RSA PRIVATE KEY-----", key_header


def http2_exchange(port, context, method, path):
    """Raw h2 client: avoids adding any transport-test-only HTTP package."""
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
                    (":method", method),
                    (":scheme", "https"),
                    (":authority", "127.0.0.1"),
                    (":path", path),
                    ("content-length", "0"),
                ],
                end_stream=True,
            )
            status, data, ended = None, b"", False
            while not ended:
                incoming = tls.recv(65535)
                assert incoming, "stream closed before END_STREAM"
                for event in connection.receive_data(incoming):
                    if isinstance(event, h2.events.ResponseReceived):
                        headers = dict(event.headers)
                        status = int(headers[":status"])
                    elif isinstance(event, h2.events.DataReceived):
                        data += event.data
                        connection.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                    elif isinstance(event, h2.events.StreamEnded):
                        ended = True
                tls.sendall(connection.data_to_send())
            assert isinstance(status, int) and 100 <= status < 600, (status, data)
            return status, data, tls.cipher()[0]


def plaintext(port, method, path):
    """Test the auxiliary HTTP/1.1 port without any extra test package."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        # http.client quotes // as /%2F by design: preserve the raw path here.
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", f"127.0.0.1:{port}")
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


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


def serve(https, health):
    """Real model-independent launcher fixture via the real SGLang CLI."""
    import sys
    from scripts import kimi_k31_granian_transport_test as launcher

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
        proc = mp.get_context("fork").Process(target=serve, args=(https, health))
        proc.start()
        results = {}
        try:
            # A real listener requires the server before any plaintext request.
            wait_port(https, proc)
            wait_port(health, proc)
            context = ssl.create_default_context(cafile=f"{cert_dir}/ca.pem")
            context.set_alpn_protocols(["h2"])
            context.load_cert_chain(f"{cert_dir}/client.crt", f"{cert_dir}/client.key")
            status, data, cipher = http2_exchange(https, context, "POST", "/stream-test")
            assert status == 200, (status, data)
            assert data == b"data: first\n\ndata: second\n\ndata: [DONE]\n\n", data
            results["SSE_H2"] = "first;second;done"
            results["MTLS_CIPHER"] = cipher

            # TLS verification stays enabled. CI's server has an explicit loopback SAN.
            status, data, _ = http2_exchange(https, context, "POST", "/generate-test")
            assert status == 200 and b'"text":"canary"' in data, (status, data)
            results["INFERENCE_MTLS"] = status

            for path in (
                "/",
                "/v1/models",
                "/v1/attestation",
                "/v1/chat/completions",
            ):
                status, body = plaintext(health, "GET", path)
                assert (status, body) == (404, b'{"detail":"Not Found"}'), (
                    path,
                    status,
                    body,
                )
            results["NO_INFERENCE_PLAINTEXT"] = 404
            for method, path, expected in (
                ("GET", "/health", 200),
                ("GET", "/health_generate", 200),
                ("GET", "//health_generate", 404),
                ("HEAD", "/health", 404),
                ("POST", "/health", 404),
                ("POST", "/generate", 404),
                ("POST", "/flush_cache", 404),
            ):
                status, _ = plaintext(health, method, path)
                assert status == expected, (method, path, status)
            results["HEALTH_ONLY_PATHS"] = "passed"

            for name, cert in (
                ("MISSING_CLIENT_CERT_REJECTED", None),
                (
                    "UNTRUSTED_CA_REJECTED",
                    (f"{cert_dir}/wrong.crt", f"{cert_dir}/wrong.key"),
                ),
            ):
                untrusted = ssl.create_default_context(cafile=f"{cert_dir}/ca.pem")
                untrusted.set_alpn_protocols(["h2"])
                if cert:
                    untrusted.load_cert_chain(*cert)
                try:
                    _, _, _ = http2_exchange(
                        https, untrusted, "GET", "/health_generate"
                    )
                    raise AssertionError(f"Broken client certificate accepted: {name}")
                except ssl.SSLError:
                    # Client-auth failure is rejected during the TLS handshake,
                    # before any application HTTP response could be generated.
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
        except BaseException:
            print("TRANSPORT_FAILURE_RESULTS", results, flush=True)
            raise
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
