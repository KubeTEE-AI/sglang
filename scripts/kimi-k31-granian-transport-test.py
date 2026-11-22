#!/usr/bin/env python3
"""Network test of SGLang's real mTLS/health transport, without a model.

The actual --enable-http2 serving validator and _run_granian_server function
are under test. A minimal model-independent ASGI app supplies synthetic
inference/SSE responses and retains the original /health_generate handler.
This is NOT a model-readiness test: that requires the real B300/Coco canary.
"""

import os
import sys
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse, StreamingResponse
from sglang.srt.arg_groups.fields.serving import Serving
from sglang.srt.arg_groups.serving_hook import handle_ssl_validation
from sglang.srt.entrypoints import http_server
from sglang.srt.managers.tokenizer_manager import ServerStatus


async def fake_health_generate(_request):
    return ORJSONResponse({"status": "unit health"})


def main():
    if len(sys.argv) != 3:
        raise ValueError("port and health port are required")
    port, health = map(int, sys.argv[1:])
    certs = os.environ["SGLANG_TEST_CERTS"]
    serving = Serving(
        host="127.0.0.1",
        port=port,
        enable_http2=True,
        ssl_client_verify=True,
        ssl_certfile=f"{certs}/server.crt",
        ssl_keyfile=f"{certs}/server.key",
        ssl_ca_certs=f"{certs}/ca.pem",
        health_only_port=health,
    )
    # Test the exact transport-only constraints of the production SGLang
    # validator; this does not call any model-resolution validation.
    handle_ssl_validation(serving)

    @app.post("/generate-test")
    async def generate():
        return ORJSONResponse({"text": "canary"})

    @app.post("/stream-test")
    async def stream():
        async def events():
            for event in ("data: first\n\n", "data: second\n\n", "data: [DONE]\n\n"):
                yield event

        return StreamingResponse(events(), media_type="text/event-stream")

    class HealthManager:
        is_generation = True
        gracefully_exit = False
        server_status = ServerStatus.Up
        last_receive_tstamp = 0
        rid_to_state = {}
        request_finished = None

        def set_server_stop_hook(self, hook):
            self.hook = hook

        async def generate_request(self, request, raw_request):
            from time import time

            self.last_receive_tstamp = time() - 1
            yield {"status": "unit health"}

    # No fake FastAPI lifespan: the transport carries a deliberately minimal
    # app, replacing model endpoints with this fixture's explicit test routes.
    http_server.app = app
    with mock.patch.object(http_server, "app", FastAPI()), mock.patch.object(
        http_server, "health_generate", fake_health_generate
    ):
        http_server.app.post("/generate-test")(generate)
        http_server.app.post("/stream-test")(stream)
        http_server._run_granian_server(
            host=serving.host,
            port=serving.port,
            log_level="info",
            http2_max_concurrent_streams=serving.http2_max_concurrent_streams,
            http2_initial_connection_window_size=serving.http2_initial_connection_window_size,
            tokenizer_manager=HealthManager(),
            ssl_certfile=serving.ssl_certfile,
            ssl_keyfile=serving.ssl_keyfile,
            ssl_ca_certs=serving.ssl_ca_certs,
            ssl_verify=serving.ssl_client_verify,
            health_only_port=serving.health_only_port,
        )


if __name__ == "__main__":
    main()
