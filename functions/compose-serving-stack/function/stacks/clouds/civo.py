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

"""The cloud half of the stack for Civo.

No generator covers Civo, so Modelplane pins the cloud half by hand,
in the same shape a generator emits. Where a component also appears on
a generated cloud, this file states the same pin, so one review moves
both halves.

Civo pre-installs no GPU stack: its GPU node images ship the NVIDIA
container toolkit but no driver, and there is no managed GPU Operator
add-on (unlike Vultr's). So this is the one hand-written cloud that
installs the GPU Operator itself: driver enabled (the operator's
containerized driver installs to /run/nvidia/driver), toolkit disabled
(baked into the node image), and device plugin disabled so its
nvidia.com/gpu ledger never books the same physical devices the DRA
driver's ResourceSlices allocate - the DRA driver stays the sole GPU
allocator. compose-civo-cluster composes no GPU readiness gate (the
operator it would observe is installed here, after the cluster XR is
Ready - gating would deadlock), so the operator chart's wait=True and
the DRA driver's depends_on are what keep GPU health on the critical
path.
"""

from function.stacks.components import Chart, Component

COMPONENTS: list[Component] = [
    Chart(
        key="cert-manager",
        release="mp-cert-manager",
        namespace="cert-manager",
        chart="cert-manager",
        repository="https://charts.jetstack.io",
        version="v1.20.2",
        # envoy-gateway in common.py depends on this chart (a cross-half
        # edge), so its Ready must mean healthy, not just deployed.
        wait=True,
        # The chart keeps its CRDs on uninstall by default, which the gateway PKI
        # needs: it composes Certificates and Issuers as provider-kubernetes
        # Objects, and removing their CRDs would stop provider-kubernetes
        # observing them to release their finalizers.
        values={"crds": {"enabled": True}},
    ),
    Chart(
        key="kube-prometheus-stack",
        release="mp-kube-prometheus-stack",
        namespace="monitoring",
        chart="kube-prometheus-stack",
        repository="https://prometheus-community.github.io/helm-charts",
        version="84.4.0",
        values={
            "fullnameOverride": "prometheus",
            "prometheus": {
                "prometheusSpec": {
                    # Discover PodMonitors across all namespaces.
                    "podMonitorSelectorNilUsesHelmValues": False,
                    "podMonitorNamespaceSelector": {},
                    # Scrape Envoy Gateway proxy pods for upstream
                    # request metrics (envoy_cluster_upstream_rq_active):
                    # in-flight requests at the proxy level.
                    "additionalScrapeConfigs": [
                        {
                            "job_name": "envoy-gateway-proxy",
                            "kubernetes_sd_configs": [
                                {
                                    "role": "pod",
                                    "namespaces": {
                                        "names": ["envoy-gateway-system"],
                                    },
                                },
                            ],
                            "relabel_configs": [
                                {
                                    "source_labels": [
                                        "__meta_kubernetes_pod_label_app_kubernetes_io_component",
                                    ],
                                    "action": "keep",
                                    "regex": "proxy",
                                },
                                {
                                    "source_labels": ["__address__"],
                                    "action": "replace",
                                    "regex": "([^:]+)(?::\\d+)?",
                                    "replacement": "$1:19001",
                                    "target_label": "__address__",
                                },
                            ],
                            "metrics_path": "/stats/prometheus",
                        },
                    ],
                },
            },
            # Disable components we don't need for observability.
            "grafana": {"enabled": False},
            "alertmanager": {"enabled": False},
        },
    ),
    Chart(
        key="node-feature-discovery",
        release="mp-node-feature-discovery",
        namespace="node-feature-discovery",
        chart="node-feature-discovery",
        repository="https://kubernetes-sigs.github.io/node-feature-discovery/charts",
        version="0.19.0",
        # gpu-operator below depends on this chart, so its Ready must
        # mean healthy, not just deployed.
        wait=True,
        # The worker must run on the very nodes it is supposed to label:
        # the DRA driver's kubelet plugin schedules only onto nodes
        # carrying an NFD GPU label (feature.node.kubernetes.io/pci-10de
        # and friends). The cluster compositions taint GPU nodes with
        # nvidia.com/gpu, and the NFD chart's worker tolerates nothing
        # by default. Without this toleration the chain breaks silently:
        # no worker on the GPU node, no pci-10de label, no kubelet
        # plugin, no ResourceSlices, and every GPU ResourceClaim stays
        # unallocatable with all components looking healthy. Exists
        # matches every taint value.
        values={
            "worker": {
                "tolerations": [
                    {
                        "key": "nvidia.com/gpu",
                        "operator": "Exists",
                        "effect": "NoSchedule",
                    },
                ],
            },
        },
    ),
    # Installs the NVIDIA driver on Civo's driverless GPU node images and
    # validates the GPU stack. Chart pin mirrors the generated clouds'.
    # wait=True makes Ready mean the operator's validator passed, which
    # the DRA driver below depends on - without a working driver its
    # kubelet plugin would publish no ResourceSlices.
    Chart(
        key="gpu-operator",
        release="mp-gpu-operator",
        namespace="gpu-operator",
        chart="gpu-operator",
        repository="https://helm.ngc.nvidia.com/nvidia",
        version="v26.3.3",
        wait=True,
        # No kube-prometheus-stack edge, unlike the generated clouds': the
        # DCGM exporter (whose ServiceMonitor needs the Prometheus CRDs)
        # is disabled below.
        depends_on=["node-feature-discovery", "cert-manager"],
        values={
            "ccManager": {"enabled": False},
            "cdi": {"default": True, "enabled": True},
            # The operator's DaemonSets must run on the GPU nodes, which
            # carry the nvidia.com/gpu taint from compose-civo-cluster.
            "daemonsets": {"tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}]},
            "dcgm": {"enabled": False},
            "dcgmExporter": {"enabled": False},
            # The DRA driver is the sole GPU allocator: the device
            # plugin's nvidia.com/gpu ledger would book the same physical
            # devices the DRA ResourceSlices allocate.
            "devicePlugin": {"enabled": False},
            # Civo GPU node images ship no driver; the operator's
            # containerized driver installs to /run/nvidia/driver, where
            # the DRA driver's nvidiaDriverRoot points. Driver pin mirrors
            # the generated clouds'. The L40S and A100 both support the
            # open kernel modules.
            "driver": {
                "enabled": True,
                "maxParallelUpgrades": 5,
                "rdma": {"enabled": False},
                "useOpenKernelModules": True,
                "version": "580.173.02",
            },
            "fullnameOverride": "gpu-operator",
            "gdrcopy": {"enabled": False},
            "gfd": {"enabled": True},
            "kataSandboxDevicePlugin": {"enabled": False},
            # No MIG on Civo's GPU shapes (L40S has no MIG; A100 pools are
            # whole-GPU).
            "migManager": {"enabled": False},
            # Standalone NFD above, matching the generated clouds' shape.
            "nfd": {"enabled": False},
            "operator": {
                "resources": {
                    "limits": {"cpu": "500m", "memory": "700Mi"},
                    "requests": {"cpu": "200m", "memory": "300Mi"},
                },
                "tolerations": [],
                "upgradeCRD": True,
            },
            # The NVIDIA container toolkit is baked into Civo's GPU node
            # images.
            "toolkit": {"enabled": False},
            "validator": {"plugin": {"env": [{"name": "WITH_WORKLOAD", "value": "false"}]}},
        },
    ),
    # Publishes each GPU node's devices as DRA ResourceSlices and
    # registers the gpu.nvidia.com DeviceClass ModelReplica
    # ResourceClaims request through. GPU allocation is opt-in;
    # ComputeDomains (multi-node NVLink) is unused and would pull in
    # extra prerequisites. Unlike Vultr and Nebius - whose node images
    # put the driver at the default root (/) - the gpu-operator above
    # installs the containerized driver to /run/nvidia/driver, so
    # nvidiaDriverRoot must point there (the GKE half does the same for
    # its non-default driver root).
    Chart(
        key="nvidia-dra-driver-gpu",
        release="mp-dra-driver-nvidia-gpu",
        namespace="nvidia-dra-driver",
        chart="dra-driver-nvidia-gpu",
        repository="oci://registry.k8s.io/dra-driver-nvidia/charts",
        version="0.4.1",
        depends_on=["gpu-operator"],
        values={
            "gpuResourcesEnabledOverride": True,
            "nvidiaDriverRoot": "/run/nvidia/driver",
            "resources": {"computeDomains": {"enabled": False}},
        },
    ),
]
