---
title: Qwen3-Coder-480B
weight: 20
description: A 480B code MoE, multi-node BF16 over EFA or single-node FP8 on SGLang.
model: Qwen/Qwen3-Coder-480B-A35B-Instruct
vendors: [Qwen]
clouds: [EKS]
accelerators: [H200]
engines: [vLLM, SGLang]
arch: MoE
precisions: [BF16, FP8]
size: 480B A35B
ctx: "32,768"
servingModes: [Standalone, LeaderWorker]
engineImages: [vllm/vllm-openai:v0.23.0, lmsysorg/sglang:v0.5.10.post1-runtime]
gpuNote: 8× per node
---
<!-- vale write-good.Passive = NO -->
A 480B code MoE (35B active), validated in two deployments. The BF16 weights
span two H200 nodes as a gang over EFA, served from a `ModelCache`. The FP8
checkpoint fits one node, so it runs as a `Standalone` engine on SGLang with no
cache.

Both deployments were run end to end; the `InferenceClass` and
`ModelDeployment` are the exact manifests from those runs. Apply the platform
side first, then the ML side. Edit the EC2 capacity reservation placeholder in
the `CapacityReservation` before applying it.

## Validated deployments

{{< validated-deployments >}}

## Platform

{{< tabs >}}
{{< tab "Multi-node (BF16)" >}}
{{< manifests "recipes/qwen3-coder/inference-class.yaml" >}}

{{< manifests path="recipes/qwen3-coder/inference-cluster.yaml" apply="false" >}}

{{< editCode >}}
```bash
curl -fsSL {{< manifest-url "recipes/qwen3-coder/inference-cluster.yaml" >}} \
  | sed 's/cr-0123456789abcdef0/$@<your-reservation-id>$@/' \
  | kubectl apply -f -
```
{{< /editCode >}}
{{< /tab >}}
{{< tab "Single-node (FP8)" >}}
{{< manifests "recipes/qwen3-coder/inference-class-fp8.yaml" >}}
{{< /tab >}}
{{< /tabs >}}

## Deployment

{{< tabs >}}
{{< tab "Multi-node (BF16)" >}}
{{< manifests "recipes/qwen3-coder/model-cache.yaml" >}}

{{< manifests "recipes/qwen3-coder/model-deployment.yaml" >}}

{{< manifests "recipes/qwen3-coder/model-service.yaml" >}}
{{< /tab >}}
{{< tab "Single-node (FP8)" >}}
{{< manifests "recipes/qwen3-coder/model-deployment-fp8.yaml" >}}

{{< manifests "recipes/qwen3-coder/model-service-fp8.yaml" >}}
{{< /tab >}}
{{< /tabs >}}
<!-- vale write-good.Passive = YES -->
