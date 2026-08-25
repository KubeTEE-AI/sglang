# KubeTEE overlay of lmsysorg/sglang:v0.5.18 (amd64).
#
# Vendors sgl-project/sglang#35936 abort-then-pop so a client disconnect
# sends AbortReq before TokenizerManager drops rid_to_state. Stock v0.5.18
# leaves the scheduler decoding to max_new_tokens (regression: #34160
# reverted #32588 after the v0.5.17 tag).
#
# Does NOT rebuild CUDA / sgl-kernel. The official image editable-installs
# from /sgl-workspace/sglang/python, so overlaying tokenizer_manager.py is
# the whole serving change. abort_request()'s rid_to_state guard is unchanged
# (scheduler abort match is prefix-based).
#
# Build (linux/amd64 only):
#   docker buildx build --platform linux/amd64 \
#     -f docker/kubetee-v0.5.18-abort-disconnect.Dockerfile \
#     -t ghcr.io/kubetee-ai/sglang:v0.5.18-abort-disconnect --push .

FROM lmsysorg/sglang:v0.5.18@sha256:bde16a8447b19e89056b9eea06c72be6c02801dc89d528c9ea90c53368fd74bf

COPY python/sglang/srt/managers/tokenizer_manager.py \
     /sgl-workspace/sglang/python/sglang/srt/managers/tokenizer_manager.py
