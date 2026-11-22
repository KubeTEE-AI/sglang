"""Restricted Uvicorn network listener for certificate-free Kubelet probes.

This is not a mount of SGLang's FastAPI app: only the two existing health
routes reach their original handler. Exec probes must not be substituted;
Confidential Containers guest policy can deny ExecProcessRequest by design.
"""

import asyncio
import socket
import threading

import uvicorn
from fastapi import Request
from fastapi.responses import ORJSONResponse


async def health_only_app(scope, receive, send):
    from sglang.srt.entrypoints.http_server import health_generate

    if scope["type"] != "http":
        return
    if scope.get("method") == "GET" and scope.get("path") in (
        "/health",
        "/health_generate",
    ):
        response = await health_generate(Request(scope, receive=receive, send=send))
    else:
        response = ORJSONResponse({"detail": "Not Found"}, status_code=404)
    await response(scope, receive, send)


class UvicornHealthServer:
    """Secondary server whose unexpected exit stops the primary mTLS server."""

    def __init__(self, host, port, on_failure):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.bind((host, port))
            self.socket.listen(2048)
        except BaseException:
            self.socket.close()
            raise
        self.server = uvicorn.Server(
            uvicorn.Config(
                health_only_app,
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
                lifespan="off",
            )
        )
        self.on_failure = on_failure
        self.stopping = False
        self.error = None
        self.shutdown = threading.Event()
        self.started = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="sglang-health-only-uvicorn", daemon=True
        )

    def _run(self):
        try:
            # Do not let the auxiliary server steal Granian's signal handling.
            self.server.install_signal_handlers = lambda: None
            asyncio.run(self.server.serve(sockets=[self.socket]))
        except BaseException as error:
            self.error = error
        finally:
            self.started.set()
            if not self.stopping:
                self.on_failure()
            self.shutdown.set()

    def start(self):
        self.thread.start()
        try:
            if not self.started.wait(30):
                raise RuntimeError(
                    "Health-only Uvicorn did not start within 30 seconds"
                )
            if not self.server.started:
                raise RuntimeError(
                    f"Health-only Uvicorn failed during startup: {self.error}"
                )
            return self
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.server.should_exit = True
        if not self.shutdown.wait(10):
            raise RuntimeError("Health-only Uvicorn did not stop within 10 seconds")
