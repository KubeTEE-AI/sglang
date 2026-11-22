#!/usr/bin/env python3
"""CI-only real SGLang TLS/health entrypoint with a non-GPU manager stub.

No real model is constructed. The real CLI, launcher, server, Uvicorn health
handler and transport paths execute; only the process factory and warmup/glue
which require a loaded model are stubbed. Not evidence of model readiness.
"""

import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

from fastapi.responses import ORJSONResponse, StreamingResponse
from sglang.srt.entrypoints import http_server
from sglang.srt.managers.tokenizer_manager import ServerStatus
from sglang.srt.server_args import prepare_server_args


def fake_launch_subprocesses(*, server_args, **kwargs):
    class Manager:
        is_generation = True
        gracefully_exit = False
        server_status = ServerStatus.Up
        last_receive_tstamp = 0
        rid_to_state = {}
        request_finished = None  # populated by the real lifespan cleanup code

        def set_server_stop_hook(self, hook):
            self.hook = hook

        async def generate_request(self, request, raw_request):
            self.last_receive_tstamp = time.time() - 1
            yield {"status": "unit health"}

    async def generate(_):
        return ORJSONResponse({"text": "canary"})

    async def stream(_):
        async def events():
            for event in ("data: first\n\n", "data: second\n\n", "data: [DONE]\n\n"):
                yield event

        return StreamingResponse(events(), media_type="text/event-stream")

    http_server.app.post("/generate-test")(generate)
    http_server.app.post("/stream-test")(stream)
    manager = Manager()
    return (
        manager,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(scheduler_infos=[{}]),
        None,
        [],
    )


def main():
    if len(sys.argv) != 3:
        raise ValueError("port and health port are required")
    port, health = map(int, sys.argv[1:])
    certs = os.environ["SGLANG_TEST_CERTS"]
    model_dir = tempfile.mkdtemp(prefix="kimi-k31-transport-model-")
    with open(os.path.join(model_dir, "config.json"), "w") as handle:
        json.dump(
            {
                "architectures": ["LlamaForCausalLM"],
                "model_type": "llama",
                "hidden_size": 16,
                "intermediate_size": 32,
                "num_attention_heads": 2,
                "num_key_value_heads": 2,
                "num_hidden_layers": 2,
                "vocab_size": 128,
                "max_position_embeddings": 2048,
            },
            handle,
        )
    argv = [
        "--model-path",
        model_dir,
        "--served-model-name",
        "unit-stub",
        "--skip-tokenizer-init",
        "--device",
        "cpu",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--enable-http2",
        "--ssl-client-verify",
        "--ssl-certfile",
        f"{certs}/server.crt",
        "--ssl-keyfile",
        f"{certs}/server.key",
        "--ssl-ca-certs",
        f"{certs}/ca.pem",
        "--health-only-port",
        str(health),
    ]
    args = prepare_server_args(argv)
    args.check_server_args()
    from sglang.srt import runtime_context

    runtime_context.publish(args, role="tokenizer")
    assert args.ssl_client_verify is True and args.health_only_port == health
    assert args.enable_http2 and args.tokenizer_worker_num == 1

    # Do not stub server_args.resolve_once(), publish(), FastAPI lifespan,
    # Granian, Uvicorn, TLS or health_generate. The first two parts perform the
    # exact CLI/resolution conversion needed by real deployment arguments.
    with (
        mock.patch.object(
            http_server.Engine, "_launch_subprocesses", fake_launch_subprocesses
        ),
        mock.patch.object(http_server, "_wait_and_warmup", lambda **kwargs: None),
        mock.patch.object(http_server, "set_uvicorn_logging_configs", lambda _: None),
    ):
        http_server.launch_server(args)


if __name__ == "__main__":
    main()
