# Hand-written: Modelplane's own pins, chosen in review rather than
# resolved from a recipe. AICR is unlikely to ever cover this file:
# its accelerator values are nine NVIDIA SKUs, its fingerprinting maps
# an AMD device to the empty string, and it fails closed on a cluster
# mixing vendors (see "Clouds AICR doesn't cover" in
# serving-stack-generation.md). This file is the vendor-neutrality
# goal made concrete - the format has no NVIDIA dependency, so an AMD
# stack is a list like any other.
#
# Stand-in for functions/compose-serving-stack/function/stacks/amd.py;
# in-tree, Chart and Manifests come from the stacks package, and the
# function joins this list with the hand-written common.py and the stack's file.
#
# Joined in place of a cloud's NVIDIA list where the cluster's pools
# are AMD; today that is Vultr's MI300X and MI325X. The provider owns
# the kernel driver (the ROCm stack ships in the node image), and
# AMD's DRA driver publishes ResourceSlices under the gpu.amd.com
# device class - so a ModelReplica's ResourceClaimTemplate must name
# gpu.amd.com here where it names gpu.nvidia.com everywhere else, and
# the membership checks that make an AMD pool on an NVIDIA stack a
# reported refusal rather than a wrong install are future work (see
# "Non-NVIDIA accelerators" in the design).
#
# Nothing upstream stands behind these pins the way a recipe stands
# behind generated/. The exporter's metric names and ServiceMonitor
# shape in particular need verifying on hardware before a release
# carries this file.

COMPONENTS: list[Component] = [
    Chart(
        key='cert-manager',
        release='mp-cert-manager',
        namespace='cert-manager',
        chart='cert-manager',
        repository='https://charts.jetstack.io',
        version='v1.20.2',
        values={
            'crds': {'enabled': True},
            'fullnameOverride': 'cert-manager',
        },
    ),
    Chart(
        key='node-feature-discovery',
        release='mp-node-feature-discovery',
        namespace='node-feature-discovery',
        chart='node-feature-discovery',
        repository='https://kubernetes-sigs.github.io/node-feature-discovery/charts',
        version='0.19.0',
    ),
    Chart(
        key='prometheus-operator-crds',
        release='mp-prometheus-operator-crds',
        namespace='monitoring',
        chart='prometheus-operator-crds',
        repository='https://prometheus-community.github.io/helm-charts',
        version='28.0.1',
    ),
    Chart(
        key='kube-prometheus-stack',
        release='mp-kube-prometheus-stack',
        namespace='monitoring',
        chart='kube-prometheus-stack',
        repository='https://prometheus-community.github.io/helm-charts',
        version='84.4.0',
        depends_on=[
            'prometheus-operator-crds',
        ],
        values={
            # CRDs travel as their own component so they upgrade on
            # their own; Helm ignores a chart's crds/ on upgrade.
            'crds': {'enabled': False},
            # prometheus-adapter's URL names the kube-prometheus-prometheus
            # Service this override produces.
            'fullnameOverride': 'kube-prometheus',
            'prometheus': {
                'prometheusSpec': {
                    'serviceMonitorNamespaceSelector': {},
                    'serviceMonitorSelectorNilUsesHelmValues': False,
                },
            },
        },
    ),
    Chart(
        key='prometheus-adapter',
        release='mp-prometheus-adapter',
        namespace='monitoring',
        chart='prometheus-adapter',
        repository='https://prometheus-community.github.io/helm-charts',
        version='5.3.0',
        depends_on=[
            'kube-prometheus-stack',
        ],
        values={
            'prometheus': {
                'port': 9090,
                'url': 'http://kube-prometheus-prometheus',
            },
            # gpu_utilization and gpu_memory_used are the names the
            # HPA path scales on - the same on every cloud and every
            # vendor. Only the series feeding them changes: AMD's
            # device-metrics-exporter in place of DCGM.
            'rules': {
                'custom': [
                    {
                        'seriesQuery': 'gpu_gfx_activity{namespace!="",pod!=""}',
                        'metricsQuery': 'last_over_time(<<.Series>>[1m])',
                        'name': {'matches': 'gpu_gfx_activity', 'as': 'gpu_utilization'},
                        'resources': {
                            'overrides': {
                                'namespace': {'resource': 'namespace'},
                                'pod': {'resource': 'pod'},
                            },
                        },
                    },
                    {
                        'seriesQuery': 'gpu_used_vram{namespace!="",pod!=""}',
                        'metricsQuery': 'last_over_time(<<.Series>>[1m])',
                        'name': {'matches': 'gpu_used_vram', 'as': 'gpu_memory_used'},
                        'resources': {
                            'overrides': {
                                'namespace': {'resource': 'namespace'},
                                'pod': {'resource': 'pod'},
                            },
                        },
                    },
                ],
            },
        },
    ),
    # DCGM has no seat here; AMD's exporter feeds the same two metric
    # names the adapter publishes. There is no gpu-operator equivalent
    # to carry it, so it is its own component.
    Chart(
        key='device-metrics-exporter',
        release='mp-device-metrics-exporter',
        namespace='device-metrics-exporter',
        chart='device-metrics-exporter',
        repository='oci://ghcr.io/rocm/device-metrics-exporter/helm-charts',
        version='v1.3.0',
        depends_on=[
            'prometheus-operator-crds',
        ],
        values={
            'serviceMonitor': {'enabled': True},
        },
    ),
    Chart(
        key='k8s-gpu-dra-driver',
        release='mp-k8s-gpu-dra-driver',
        namespace='amd-dra-driver',
        chart='k8s-gpu-dra-driver',
        repository='https://rocm.github.io/k8s-gpu-dra-driver',
        version='v1.0.1',
        depends_on=[
            'node-feature-discovery',
        ],
        # No values Modelplane must own: the driver comes from the
        # node image, and the chart's default is whole-GPU allocation
        # via ResourceSlices - the mode the NVIDIA half needs three
        # managed values to select.
    ),
]
