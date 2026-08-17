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
precision: BF16/FP8
size: 480B A35B
ctx: "32,768"
servingModes: [Standalone, LeaderWorker]
engineImages: [vllm/vllm-openai:v0.23.0, lmsysorg/sglang:v0.5.10.post1-runtime]
variants: ["BF16 multi-node · vLLM", "FP8 single-node · SGLang"]
gpuNote: 8× per node
---
<!-- vale write-good.Passive = NO -->
A 480B code MoE (35B active). Two validated shapes: the BF16 weights span two
H200 nodes as a gang over EFA, served from a `ModelCache`; the FP8 checkpoint
fits one node, so it runs as a single `Standalone` engine on SGLang with no
cache.

Both shapes were run end to end; the `InferenceClass` and `ModelDeployment` are
the exact manifests from those runs. Apply the platform side first, then the ML
side. The `InferenceCluster` carries an EC2 capacity reservation placeholder to
edit before applying.

## Validated deployments

{{< validated-deployments >}}

## Platform

{{< tabs >}}
{{< tab "Multi-node (BF16)" >}}
{{< manifests "examples/qwen3-coder/inference-class.yaml" >}}

{{< manifests path="examples/qwen3-coder/inference-cluster.yaml" apply="false" >}}

{{< editCode >}}
```bash
curl -fsSL {{< manifest-url "examples/qwen3-coder/inference-cluster.yaml" >}} \
  | sed 's/cr-0123456789abcdef0/$@<your-reservation-id>$@/' \
  | kubectl apply -f -
```
{{< /editCode >}}
{{< /tab >}}
{{< tab "Single-node (FP8)" >}}
{{< manifests "examples/qwen3-coder/inference-class-fp8.yaml" >}}
{{< /tab >}}
{{< /tabs >}}

## Deployment

{{< tabs >}}
{{< tab "Multi-node (BF16)" >}}
{{< manifests "examples/qwen3-coder/model-cache.yaml" >}}

{{< manifests "examples/qwen3-coder/model-deployment.yaml" >}}

{{< manifests "examples/qwen3-coder/model-service.yaml" >}}
{{< /tab >}}
{{< tab "Single-node (FP8)" >}}
{{< manifests "examples/qwen3-coder/model-deployment-fp8.yaml" >}}

{{< manifests "examples/qwen3-coder/model-service-fp8.yaml" >}}
{{< /tab >}}
{{< /tabs >}}
<!-- vale write-good.Passive = YES -->
