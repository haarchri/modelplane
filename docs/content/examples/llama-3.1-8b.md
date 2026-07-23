---
title: Llama-3.1-8B
weight: 40
description: An 8B dense chat model on a single NVIDIA L4.
---
<!-- vale write-good.Passive = NO -->
An 8B dense chat model on a single NVIDIA L4. The entry recipe: one `Standalone`
engine, no cache, public weights from a Hugging Face mirror. It carries no
`clusterSelector`, so device capacity alone matches it to any compatible L4 in
the fleet.

This recipe was run end to end on GKE; the `InferenceClass`, `InferenceCluster`,
and `ModelDeployment` are the exact manifests from that run. The EKS platform
shape is the standard single-L4 recipe. It passes server validation but was not
served in this run. Apply the platform side first, then the ML side. The GKE
`InferenceCluster` carries a GCP project placeholder to edit before applying.

## Platform

{{< tabs >}}
{{< tab "EKS" >}}
{{< manifests "examples/llama-3.1-8b/inference-class-eks.yaml" >}}

{{< manifests "examples/llama-3.1-8b/inference-cluster-eks.yaml" >}}
{{< /tab >}}
{{< tab "GKE" >}}
{{< manifests "examples/llama-3.1-8b/inference-class-gke.yaml" >}}

{{< manifests "examples/llama-3.1-8b/inference-cluster-gke.yaml" >}}
{{< /tab >}}
{{< /tabs >}}

## Deployment

{{< manifests "examples/llama-3.1-8b/model-deployment.yaml" >}}

{{< manifests "examples/llama-3.1-8b/model-service.yaml" >}}
<!-- vale write-good.Passive = YES -->
