# Copyright 2026 The Modelplane Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the compose-civo-cluster function."""

import dataclasses
import unittest
from typing import Any

from crossplane.function import logging, resource
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from function import fn
from google.protobuf import duration_pb2 as durationpb
from google.protobuf import json_format
from google.protobuf import struct_pb2 as structpb
from models.ai.modelplane.infrastructure.civocluster import v1alpha1
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as metav1


@dataclasses.dataclass
class Case:
    """A test case for compose-civo-cluster."""

    name: str
    req: fnv1.RunFunctionRequest
    want: fnv1.RunFunctionResponse


def setUpModule() -> None:
    logging.configure(level=logging.Level.DISABLED)


# Name of the cluster's connection secret. Derived like the function derives
# it - the hash suffix depends only on the parent and child names.
_KUBECONFIG_SECRET_NAME = resource.child_name("test-cluster", "kubeconfig")

# The system node pool injected inline into every cluster.
_SYSTEM_POOL = {
    "label": "system",
    "size": "g4p.kube.small",
    "nodeCount": 2,
    "labels": {"modelplane.ai/pool": "system"},
}

# The taint every GPU pool carries.
_GPU_TAINT = [
    {"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"},
]

# Civo IDs the provider writes to external-name annotations once resources
# exist. The autoscaler addresses node groups by pool ID.
_CLUSTER_ID = "11111111-2222-3333-4444-555555555555"
_GPU_POOL_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _xr(pools: list[v1alpha1.NodePool]) -> dict:
    """A CivoCluster XR with the given node pools, as a request dict."""
    return v1alpha1.CivoCluster(
        metadata=metav1.ObjectMeta(
            name="test-cluster",
            namespace="modelplane-system",
        ),
        spec=v1alpha1.Spec(
            region="LON1",
            nodePools=pools,
        ),
    ).model_dump(exclude_none=True, mode="json")


def _req(
    pools: list[v1alpha1.NodePool],
    observed_resources: dict[str, fnv1.Resource] | None = None,
) -> fnv1.RunFunctionRequest:
    return fnv1.RunFunctionRequest(
        observed=fnv1.State(
            composite=fnv1.Resource(resource=resource.dict_to_struct(_xr(pools))),
            resources=observed_resources or {},
        ),
    )


def _network(
    cred_kind: str = "ClusterProviderConfig",
    cred_name: str = "default",
) -> dict:
    """A Network golden."""
    return {
        "apiVersion": "vpc.civo.m.upbound.io/v1beta1",
        "kind": "Network",
        "spec": {
            "providerConfigRef": {"kind": cred_kind, "name": cred_name},
            "forProvider": {
                "label": "test-cluster",
                "region": "LON1",
            },
        },
    }


def _firewall(
    cred_kind: str = "ClusterProviderConfig",
    cred_name: str = "default",
) -> dict:
    """A Firewall golden with Civo's default rules."""
    return {
        "apiVersion": "vpc.civo.m.upbound.io/v1beta1",
        "kind": "Firewall",
        "spec": {
            "providerConfigRef": {"kind": cred_kind, "name": cred_name},
            "forProvider": {
                "name": "test-cluster",
                "region": "LON1",
                "createDefaultRules": True,
                "networkIdSelector": {"matchControllerRef": True},
            },
        },
    }


def _cluster(
    cred_kind: str = "ClusterProviderConfig",
    cred_name: str = "default",
) -> dict:
    """A Cluster golden with only the system pool."""
    return {
        "apiVersion": "kubernetes.civo.m.upbound.io/v1beta1",
        "kind": "Cluster",
        "spec": {
            "providerConfigRef": {"kind": cred_kind, "name": cred_name},
            "forProvider": {
                "name": "test-cluster",
                "region": "LON1",
                "cni": "cilium",
                "applications": "-traefik2-nodeport",
                "writeKubeconfig": True,
                "networkIdSelector": {"matchControllerRef": True},
                "firewallIdSelector": {"matchControllerRef": True},
                "pools": _SYSTEM_POOL,
            },
            "writeConnectionSecretToRef": {"name": _KUBECONFIG_SECRET_NAME},
        },
    }


def _provider_config_helm() -> dict:
    """A provider-helm ProviderConfig golden pointing at the kubeconfig."""
    return {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "ProviderConfig",
        "metadata": {
            "name": _KUBECONFIG_SECRET_NAME,
            "namespace": "modelplane-system",
        },
        "spec": {
            "credentials": {
                "source": "Secret",
                "secretRef": {
                    "namespace": "modelplane-system",
                    "name": _KUBECONFIG_SECRET_NAME,
                    "key": "kubeconfig",
                },
            },
        },
    }


def _autoscaler(groups: list[dict]) -> dict:
    """The cluster autoscaler Release golden for the given node groups."""
    return {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {"namespace": "modelplane-system"},
        "spec": {
            "managementPolicies": ["Observe", "Create", "Update"],
            "providerConfigRef": {
                "kind": "ProviderConfig",
                "name": _KUBECONFIG_SECRET_NAME,
            },
            "forProvider": {
                "chart": {
                    "name": "cluster-autoscaler",
                    "repository": "https://kubernetes.github.io/autoscaler",
                    "version": "9.57.0",
                },
                "namespace": "kube-system",
                "values": {
                    "cloudProvider": "civo",
                    "autoscalingGroups": groups,
                    "secretKeyRefNameOverride": "civo-api-access",
                },
            },
        },
    }


def _node_pool(
    label: str,
    size: str,
    node_count: int,
    labels: dict[str, str],
    taint: list | None = None,
    cred_kind: str = "ClusterProviderConfig",
    cred_name: str = "default",
    *,
    autoscaled: bool = False,
) -> dict:
    """A NodePool golden. Autoscaled pools seed nodeCount via initProvider
    so the autoscaler owns it after creation; fixed pools keep it in
    forProvider."""
    fp: dict[str, Any] = {
        "label": label,
        "size": size,
        "region": "LON1",
        "labels": labels,
        "clusterIdSelector": {"matchControllerRef": True},
    }
    if taint:
        fp["taint"] = taint
    spec: dict[str, Any] = {
        "providerConfigRef": {"kind": cred_kind, "name": cred_name},
        "forProvider": fp,
    }
    if autoscaled:
        spec["managementPolicies"] = ["Observe", "Create", "Update", "Delete"]
        spec["initProvider"] = {"nodeCount": node_count}
    else:
        fp["nodeCount"] = node_count
    return {
        "apiVersion": "kubernetes.civo.m.upbound.io/v1beta1",
        "kind": "NodePool",
        "spec": spec,
    }


def _status() -> dict:
    return {
        "status": {
            "secrets": [
                {
                    "type": "Kubeconfig",
                    "name": _KUBECONFIG_SECRET_NAME,
                    "key": "kubeconfig",
                },
            ],
        },
    }


def _observed(desired: dict, ready: str, external_name: str | None = None) -> fnv1.Resource:
    """An observed variant of a desired resource with a Ready condition and
    optionally the external-name annotation the provider sets."""
    observed: dict[str, Any] = {
        **desired,
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": ready,
                    "reason": "Available" if ready == "True" else "Unavailable",
                    "lastTransitionTime": "2024-01-01T00:00:00Z",
                },
            ],
        },
    }
    if external_name is not None:
        metadata = {**observed.get("metadata", {})}
        metadata["annotations"] = {"crossplane.io/external-name": external_name}
        observed["metadata"] = metadata
    return fnv1.Resource(resource=resource.dict_to_struct(observed))


def _observed_ready(desired: dict, external_name: str | None = None) -> fnv1.Resource:
    return _observed(desired, "True", external_name)


def _observed_unready(desired: dict, external_name: str | None = None) -> fnv1.Resource:
    return _observed(desired, "False", external_name)


_GPU_POOL = v1alpha1.NodePool(
    name="gpu-l40s",
    role="GPU",
    size="an.g1.l40s.kube.x1",
    maxNodeCount=4,
    gpu=v1alpha1.Gpu(acceleratorType="nvidia-l40s"),
)

_GPU_POOL_GOLDEN = _node_pool(
    label="gpu-l40s",
    size="an.g1.l40s.kube.x1",
    node_count=1,
    labels={
        "modelplane.ai/pool": "gpu-l40s",
        "modelplane.ai/gpu": "nvidia-l40s",
    },
    taint=_GPU_TAINT,
    autoscaled=True,
)


class TestFunctionRunner(unittest.IsolatedAsyncioTestCase):
    """Tests for FunctionRunner.RunFunction."""

    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = fn.FunctionRunner()

    async def test_compose(self) -> None:
        """The function composes Civo cluster infrastructure."""
        cases = [
            Case(
                name="network, firewall and cluster composed first; node pools withheld until cluster Ready",
                req=_req([_GPU_POOL]),
                want=fnv1.RunFunctionResponse(
                    meta=fnv1.ResponseMeta(ttl=durationpb.Duration(seconds=60)),
                    desired=fnv1.State(
                        composite=fnv1.Resource(resource=resource.dict_to_struct(_status())),
                        resources={
                            "network": fnv1.Resource(
                                resource=resource.dict_to_struct(_network()),
                            ),
                            "firewall": fnv1.Resource(
                                resource=resource.dict_to_struct(_firewall()),
                            ),
                            "cluster": fnv1.Resource(
                                resource=resource.dict_to_struct(_cluster()),
                            ),
                        },
                    ),
                    context=structpb.Struct(),
                ),
            ),
            Case(
                name="node pools and provider config composed once cluster is Ready; autoscaler has no groups until pool IDs observed",
                req=_req(
                    [_GPU_POOL],
                    observed_resources={
                        "cluster": _observed_ready(_cluster(), external_name=_CLUSTER_ID),
                    },
                ),
                want=fnv1.RunFunctionResponse(
                    meta=fnv1.ResponseMeta(ttl=durationpb.Duration(seconds=60)),
                    desired=fnv1.State(
                        composite=fnv1.Resource(resource=resource.dict_to_struct(_status())),
                        resources={
                            "network": fnv1.Resource(
                                resource=resource.dict_to_struct(_network()),
                            ),
                            "firewall": fnv1.Resource(
                                resource=resource.dict_to_struct(_firewall()),
                            ),
                            "cluster": fnv1.Resource(
                                resource=resource.dict_to_struct(_cluster()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "node-pool-gpu-l40s": fnv1.Resource(
                                resource=resource.dict_to_struct(_GPU_POOL_GOLDEN),
                            ),
                            "provider-config-helm": fnv1.Resource(
                                resource=resource.dict_to_struct(_provider_config_helm()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "release-cluster-autoscaler": fnv1.Resource(
                                resource=resource.dict_to_struct(_autoscaler([])),
                            ),
                        },
                    ),
                    context=structpb.Struct(),
                ),
            ),
            Case(
                name="autoscaler release composed from observed pool IDs",
                req=_req(
                    [_GPU_POOL],
                    observed_resources={
                        "cluster": _observed_ready(_cluster(), external_name=_CLUSTER_ID),
                        "node-pool-gpu-l40s": _observed_ready(_GPU_POOL_GOLDEN, external_name=_GPU_POOL_ID),
                    },
                ),
                want=fnv1.RunFunctionResponse(
                    meta=fnv1.ResponseMeta(ttl=durationpb.Duration(seconds=60)),
                    desired=fnv1.State(
                        composite=fnv1.Resource(resource=resource.dict_to_struct(_status())),
                        resources={
                            "network": fnv1.Resource(
                                resource=resource.dict_to_struct(_network()),
                            ),
                            "firewall": fnv1.Resource(
                                resource=resource.dict_to_struct(_firewall()),
                            ),
                            "cluster": fnv1.Resource(
                                resource=resource.dict_to_struct(_cluster()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "node-pool-gpu-l40s": fnv1.Resource(
                                resource=resource.dict_to_struct(_GPU_POOL_GOLDEN),
                                ready=fnv1.READY_TRUE,
                            ),
                            "provider-config-helm": fnv1.Resource(
                                resource=resource.dict_to_struct(_provider_config_helm()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "release-cluster-autoscaler": fnv1.Resource(
                                resource=resource.dict_to_struct(
                                    _autoscaler(
                                        [{"name": _GPU_POOL_ID, "minSize": 1, "maxSize": 4}],
                                    ),
                                ),
                            ),
                        },
                    ),
                    context=structpb.Struct(),
                ),
            ),
            Case(
                name="dependents kept when the cluster Ready condition transiently regresses",
                req=_req(
                    [_GPU_POOL],
                    observed_resources={
                        "cluster": _observed_unready(_cluster(), external_name=_CLUSTER_ID),
                        "provider-config-helm": _observed_ready(_provider_config_helm()),
                    },
                ),
                want=fnv1.RunFunctionResponse(
                    meta=fnv1.ResponseMeta(ttl=durationpb.Duration(seconds=60)),
                    desired=fnv1.State(
                        composite=fnv1.Resource(resource=resource.dict_to_struct(_status())),
                        resources={
                            "network": fnv1.Resource(
                                resource=resource.dict_to_struct(_network()),
                            ),
                            "firewall": fnv1.Resource(
                                resource=resource.dict_to_struct(_firewall()),
                            ),
                            "cluster": fnv1.Resource(
                                resource=resource.dict_to_struct(_cluster()),
                            ),
                            "node-pool-gpu-l40s": fnv1.Resource(
                                resource=resource.dict_to_struct(_GPU_POOL_GOLDEN),
                            ),
                            "provider-config-helm": fnv1.Resource(
                                resource=resource.dict_to_struct(_provider_config_helm()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "release-cluster-autoscaler": fnv1.Resource(
                                resource=resource.dict_to_struct(_autoscaler([])),
                            ),
                        },
                    ),
                    context=structpb.Struct(),
                ),
            ),
            Case(
                name="fixed-size GPU pool composes the autoscaler release with no node groups",
                req=_req(
                    [
                        v1alpha1.NodePool(
                            name="gpu-l40s",
                            role="GPU",
                            size="an.g1.l40s.kube.x1",
                            nodeCount=2,
                            gpu=v1alpha1.Gpu(acceleratorType="nvidia-l40s"),
                        ),
                    ],
                    observed_resources={
                        "cluster": _observed_ready(_cluster(), external_name=_CLUSTER_ID),
                    },
                ),
                want=fnv1.RunFunctionResponse(
                    meta=fnv1.ResponseMeta(ttl=durationpb.Duration(seconds=60)),
                    desired=fnv1.State(
                        composite=fnv1.Resource(resource=resource.dict_to_struct(_status())),
                        resources={
                            "network": fnv1.Resource(
                                resource=resource.dict_to_struct(_network()),
                            ),
                            "firewall": fnv1.Resource(
                                resource=resource.dict_to_struct(_firewall()),
                            ),
                            "cluster": fnv1.Resource(
                                resource=resource.dict_to_struct(_cluster()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "node-pool-gpu-l40s": fnv1.Resource(
                                resource=resource.dict_to_struct(
                                    _node_pool(
                                        label="gpu-l40s",
                                        size="an.g1.l40s.kube.x1",
                                        node_count=2,
                                        labels={
                                            "modelplane.ai/pool": "gpu-l40s",
                                            "modelplane.ai/gpu": "nvidia-l40s",
                                        },
                                        taint=_GPU_TAINT,
                                    ),
                                ),
                            ),
                            "provider-config-helm": fnv1.Resource(
                                resource=resource.dict_to_struct(_provider_config_helm()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "release-cluster-autoscaler": fnv1.Resource(
                                resource=resource.dict_to_struct(_autoscaler([])),
                            ),
                        },
                    ),
                    context=structpb.Struct(),
                ),
            ),
            Case(
                name="System pool carries no taint; credentials override propagates",
                req=fnv1.RunFunctionRequest(
                    observed=fnv1.State(
                        composite=fnv1.Resource(
                            resource=resource.dict_to_struct(
                                v1alpha1.CivoCluster(
                                    metadata=metav1.ObjectMeta(
                                        name="test-cluster",
                                        namespace="modelplane-system",
                                    ),
                                    spec=v1alpha1.Spec(
                                        region="LON1",
                                        credentials=v1alpha1.Credentials(
                                            type="ProviderConfig",
                                            name="team-a",
                                        ),
                                        nodePools=[
                                            v1alpha1.NodePool(
                                                name="workers",
                                                role="System",
                                                size="g4p.kube.small",
                                                nodeCount=2,
                                            ),
                                        ],
                                    ),
                                ).model_dump(exclude_none=True, mode="json"),
                            ),
                        ),
                        resources={
                            "cluster": _observed_ready(
                                _cluster(cred_kind="ProviderConfig", cred_name="team-a"),
                                external_name=_CLUSTER_ID,
                            ),
                        },
                    ),
                ),
                want=fnv1.RunFunctionResponse(
                    meta=fnv1.ResponseMeta(ttl=durationpb.Duration(seconds=60)),
                    desired=fnv1.State(
                        composite=fnv1.Resource(resource=resource.dict_to_struct(_status())),
                        resources={
                            "network": fnv1.Resource(
                                resource=resource.dict_to_struct(
                                    _network(cred_kind="ProviderConfig", cred_name="team-a"),
                                ),
                            ),
                            "firewall": fnv1.Resource(
                                resource=resource.dict_to_struct(
                                    _firewall(cred_kind="ProviderConfig", cred_name="team-a"),
                                ),
                            ),
                            "cluster": fnv1.Resource(
                                resource=resource.dict_to_struct(
                                    _cluster(cred_kind="ProviderConfig", cred_name="team-a"),
                                ),
                                ready=fnv1.READY_TRUE,
                            ),
                            "node-pool-workers": fnv1.Resource(
                                resource=resource.dict_to_struct(
                                    _node_pool(
                                        label="workers",
                                        size="g4p.kube.small",
                                        node_count=2,
                                        labels={"modelplane.ai/pool": "workers"},
                                        cred_kind="ProviderConfig",
                                        cred_name="team-a",
                                    ),
                                ),
                            ),
                            "provider-config-helm": fnv1.Resource(
                                resource=resource.dict_to_struct(_provider_config_helm()),
                                ready=fnv1.READY_TRUE,
                            ),
                            "release-cluster-autoscaler": fnv1.Resource(
                                resource=resource.dict_to_struct(_autoscaler([])),
                            ),
                        },
                    ),
                    context=structpb.Struct(),
                ),
            ),
        ]

        for case in cases:
            with self.subTest(case.name):
                got = await self.runner.RunFunction(case.req, None)
                self.assertEqual(
                    json_format.MessageToDict(case.want),
                    json_format.MessageToDict(got),
                    "-want, +got",
                )
