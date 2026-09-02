# Hand-written: Modelplane's own pins, chosen in review rather than
# resolved from a recipe. AICR has no `service` value for Vultr (see
# "Clouds AICR doesn't cover" in serving-stack-generation.md).
#
# Stand-in for functions/compose-serving-stack/function/stacks/vultr.py;
# in-tree, Chart and Manifests come from the stacks package, and the
# function joins this list with the hand-written common.py and the stack's file.
#
# This is the NVIDIA half of Vultr. VKE's GPU node images carry the
# kernel driver and container toolkit, so the operator installs
# neither and the driver sits at the default root. Vultr exposes no
# accelerated fabric, so nothing here answers one. Vultr's AMD pools
# (MI300X, MI325X) join stacks/amd.py in place of this file - the
# membership checks that make an AMD pool on this stack a reported
# refusal are future work (see "Non-NVIDIA accelerators" in the
# design).
#
# Where a component is AICR's on the generated clouds (cert-manager,
# NFD, the monitoring stack, GPU Operator, the DRA driver), this file
# states the same pin, so one review moves both halves and drift shows
# up as a diff. NVSentinel, nodewright and the ephemeral-storage
# metrics are absent: they arrive on covered clouds with recipe
# evidence behind them, and nothing stands behind them here. They join
# this list when AICR grows a `service` value for Vultr - the
# external-overlay-sources ask under "Upstreaming" in the design - and
# the generator takes the file over.

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
            # HPA path scales on - the same on every cloud, whatever
            # exporter feeds them.
            'rules': {
                'custom': [
                    {
                        'seriesQuery': 'DCGM_FI_DEV_GPU_UTIL{namespace!="",pod!=""}',
                        'metricsQuery': 'last_over_time(<<.Series>>[1m])',
                        'name': {'matches': 'DCGM_FI_DEV_GPU_UTIL', 'as': 'gpu_utilization'},
                        'resources': {
                            'overrides': {
                                'namespace': {'resource': 'namespace'},
                                'pod': {'resource': 'pod'},
                            },
                        },
                    },
                    {
                        'seriesQuery': 'DCGM_FI_DEV_FB_USED{namespace!="",pod!=""}',
                        'metricsQuery': 'last_over_time(<<.Series>>[1m])',
                        'name': {'matches': 'DCGM_FI_DEV_FB_USED', 'as': 'gpu_memory_used'},
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
    Chart(
        key='gpu-operator',
        release='mp-gpu-operator',
        namespace='gpu-operator',
        chart='gpu-operator',
        repository='https://helm.ngc.nvidia.com/nvidia',
        version='v26.3.3',
        depends_on=[
            'node-feature-discovery',
            'cert-manager',
            'kube-prometheus-stack',
        ],
        values={
            # The node image provides the driver and toolkit, and the
            # device plugin would double-advertise GPUs the DRA driver
            # publishes - the managed paths generate.py asserts on
            # every generated cloud, restated here by hand.
            'driver': {'enabled': False},
            'toolkit': {'enabled': False},
            'devicePlugin': {'enabled': False},
            'gdrcopy': {'enabled': False},
        },
    ),
    Chart(
        key='nvidia-dra-driver-gpu',
        release='mp-dra-driver-nvidia-gpu',
        namespace='nvidia-dra-driver',
        chart='dra-driver-nvidia-gpu',
        repository='oci://registry.k8s.io/dra-driver-nvidia/charts',
        version='0.4.1',
        depends_on=[
            'gpu-operator',
        ],
        values={
            'fullnameOverride': 'nvidia-dra-driver-gpu',
            'nameOverride': 'nvidia-dra-driver-gpu',
            # Modelplane allocates GPUs via DRA ResourceSlices;
            # multi-node NVLink is unused.
            'gpuResourcesEnabledOverride': True,
            'resources': {
                'gpus': {'enabled': True},
                'computeDomains': {'enabled': False},
            },
            'nvidiaDriverRoot': '/',
        },
    ),
]
