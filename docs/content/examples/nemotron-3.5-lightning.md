---
title: Nemotron-3.5-Lightning
weight: 15
description: An open 30B MoE with 3B active parameters served NVFP4 on a single H100 on Nebius.
---
<!-- vale write-good.Passive = NO -->
NVIDIA's Nemotron-3.5-Lightning, an open 30B mixture-of-experts model with 3B
active parameters built for the execution layer of long-running agents, served
NVFP4 as a single `Standalone` vLLM engine on one H100 80GB node on Nebius.
The NVFP4 checkpoint (~20 GiB) fits a single GPU with headroom for the KV and
Mamba caches, so the engine needs no tensor parallelism, no gang, and no
prefill/decode disaggregation. Weights stage once to a `ModelCache` on a
Nebius shared filesystem and mount at `/mnt/models`.

This recipe was run end to end on Nebius (`eu-north`): serving and tool
calling validated on a single H100 node.
`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` is a public repository
(OpenMDW-1.1), so no Hugging Face token or Secret is needed. Apply the
platform side first, then the ML side.

## Platform

{{< manifests "examples/nemotron-3.5-lightning/inference-class-nebius.yaml" >}}

{{< manifests "examples/nemotron-3.5-lightning/inference-cluster-nebius.yaml" >}}

## Deployment

{{< manifests "examples/nemotron-3.5-lightning/model-cache.yaml" >}}

{{< manifests "examples/nemotron-3.5-lightning/model-deployment.yaml" >}}

{{< manifests "examples/nemotron-3.5-lightning/model-service.yaml" >}}
<!-- vale write-good.Passive = YES -->
