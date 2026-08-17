---
title: GLM-4.5-Air
weight: 25
description: A 106B MoE served from a GGUF quant via llama.cpp on a single A100.
model: unsloth/GLM-4.5-Air-GGUF:IQ4_XS
vendors: [Z.ai]
clouds: [GKE]
accelerators: [A100]
engines: [llama.cpp]
arch: MoE
precision: GGUF IQ4_XS
size: 106B A12B
ctx: "8,192"
servingModes: [Standalone]
engineImages: [ghcr.io/ggml-org/llama.cpp:server-cuda]
variants: ["GGUF IQ4_XS · GKE"]
gpuNote: 1× per node
---
<!-- vale write-good.Passive = NO -->
A 106B MoE served from an Unsloth GGUF quant via llama.cpp instead of vLLM, on
a single A100 40GB. Modelplane treats the engine as any OpenAI-compatible
container, so the only changes from a vLLM deployment are the image and args:
the container is still named `engine` and listens on `:8000`. vLLM can't load
Unsloth's dynamic quants; llama.cpp can, and `-hf` pulls the quant straight
from Hugging Face at startup, so no `ModelCache` is needed for a one-off.

The model is bigger than one A100's VRAM, so `--n-cpu-moe` offloads the MoE
expert tensors to host RAM and the GPU runs the active path and KV cache.
That's how a 106B model fits one A100 instead of a multi-GPU node. Apply the
platform side first, then the ML side.

## Validated deployments

{{< validated-deployments >}}

## Platform

{{< manifests "examples/glm-4.5-air/inference-class.yaml" >}}

{{< manifests "examples/glm-4.5-air/inference-cluster.yaml" >}}

## Deployment

{{< manifests "examples/glm-4.5-air/model-deployment.yaml" >}}

{{< manifests "examples/glm-4.5-air/model-service.yaml" >}}
<!-- vale write-good.Passive = YES -->
