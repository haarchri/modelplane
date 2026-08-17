---
title: Qwen3-8B
weight: 10
description: An 8.2B dense chat model on a single NVIDIA L4.
model: Qwen/Qwen3-8B
vendors: [Qwen]
clouds: [EKS]
accelerators: [L4]
engines: [vLLM]
arch: Dense
precision: BF16
size: 8B
ctx: "16,384"
servingModes: [Standalone]
engineImages: [vllm/vllm-openai:v0.23.0]
variants: ["BF16 · EKS"]
gpuNote: 1× per node
---
<!-- vale write-good.Passive = NO -->
An 8.2B dense chat model on a single NVIDIA L4. The smallest recipe: one
`Standalone` engine, no cache, weights pulled straight from Hugging Face.

This recipe was run end to end; the `InferenceClass` and `ModelDeployment` are
the exact manifests from that run. Apply the platform side first, then the ML
side.

## Validated deployments

{{< validated-deployments >}}

## Platform

{{< manifests "examples/qwen3-8b/inference-class.yaml" >}}

{{< manifests "examples/qwen3-8b/inference-cluster.yaml" >}}

## Deployment

{{< manifests "examples/qwen3-8b/model-deployment.yaml" >}}

{{< manifests "examples/qwen3-8b/model-service.yaml" >}}
<!-- vale write-good.Passive = YES -->
