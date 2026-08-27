# Bringing Dynamo to Modelplane — a proposal

Modelplane is an open source control plane for AI inference. You install it in
your own environment, and it operates your GPU clusters — across cloud, neocloud,
and on-premise — as one inference fleet: provisioning clusters, scheduling
deployments, scaling replicas, caching weights, and routing through a single
OpenAI-compatible endpoint. Built on Crossplane, it continuously
reconciles that fleet toward the state you declare, and runs any model on any
engine on any infrastructure, from a single GPU to disaggregated, multi-node
serving. Platform teams declare the fleet as `InferenceClusters`; developers
declare a `ModelDeployment` — a model's engines (vLLM, SGLang, TRT-LLM) laid out
across GPUs and nodes, plus a replica count. Today Modelplane composes that
serving layer from Kubernetes and Gateway API primitives.

Modelplane orchestrates the inference ecosystem rather than replacing it — the
models, the engines that serve them, and the infrastructure underneath. It
composes and operates a serving layer across the whole fleet, but it isn't a
serving layer itself. Dynamo is. The two work at different layers: Modelplane
places, scales, caches, and fronts a model across clusters and clouds, while a
serving stack owns what happens inside a single cluster — turning a stock engine
into a routable serving instance. We want that stack to be Dynamo.
This document proposes how, and what we'd need from the Dynamo team to get there.

The proposal is phased, because we think we can start delivering value on both
sides now rather than waiting on a single large integration:

1. **Now — à la carte.** A platform team opts a cluster into a `Dynamo` serving
   stack, and Modelplane drives stock engines with Dynamo's standalone
   components: Grove + kai-scheduler for multi-node placement, ModelExpress for
   weight distribution. No change to the ML-facing API.
2. **In parallel — the asks.** Dynamo delivers two features (one already on the
   roadmap) that let a `ModelDeployment` land on a full `DynamoGraphDeployment`
   (DGD) without changing that API or what the engine runs.
3. **Then — the DGD.** The same `Dynamo` cluster opt-in graduates to composing
   full DGDs, so Modelplane's users get Dynamo's frontend and routing while the
   API they write stays exactly what it is today.

One field on the cluster (`spec.stack: Dynamo`), one backend that evolves
underneath it.

## 1. Why Dynamo in Modelplane

Each of the following closes a gap in what Modelplane can do today.

**Topology-aware placement and multi-role startup ordering.** A model sharded
across many GPUs and nodes is where placement against the interconnect (NVLink
domain → rack → zone) decides throughput. Modelplane has no model of that
hierarchy today, and can't gang-schedule a multi-node job, so a large deployment
can land split across domains or stall half-placed. Grove and kai-scheduler give
us topology-aware, all-or-nothing placement — plus the cross-role startup
ordering some engines need.

**Faster cold starts (ModelExpress).** When Modelplane scales a deployment up,
each new replica loads its weights from storage — the slowest part of coming
online, and a source of contention when many replicas start at once. ModelExpress
lets a scaling-up replica skip that read entirely.

**Checkpoint/restore cold starts (Snapshot).** Bringing an engine online means
building the CUDA context, compiling kernels, and loading weights before it can
serve — time Modelplane pays on every scale-up. Snapshot restores a checkpoint of
a fully initialized worker instead, cutting that startup to a restore.

**GPU-memory sharing and failover (GMS).** Modelplane has no fast-recovery story
today: when an engine crashes, its replacement reloads weights from disk. GMS
keeps a model's weights resident so a restarting or standby engine re-attaches
instead of reloading — the basis for active-passive failover, which Modelplane
can't offer today.

**Mid-stream request migration.** Today, if a worker dies mid-generation, the
best Modelplane can do is retry the whole request, and a long in-flight
generation is lost. Dynamo's frontend replays the delivered tokens to a new
worker and continues the stream — valuable for longer-context and agentic
workloads.

Topology-aware placement and ModelExpress work against a stock, unmodified
engine, so Modelplane can adopt them immediately (§3). Mid-stream migration
needs Dynamo's frontend-and-wrapped-engine architecture — for us, a composed DGD
(§2). GMS and Snapshot could too, but we'd rather get them from a DGD than
rebuild their supporting machinery ourselves (§3).

## 2. Desired end goal: Modelplane's API unchanged, powered by a DGD

A platform team turns on the Dynamo stack for a cluster, and every
`ModelDeployment` scheduled there runs on a `DynamoGraphDeployment` Modelplane
composes — Dynamo's frontend, KV-aware router, disaggregated prefill/decode, and
mid-stream migration included. Choosing Dynamo is a deliberate, first-class
decision at the cluster level. The developer's side of the API stays constant.

An ML developer writes a `ModelDeployment` with `Standalone`/`Leader`/`Worker`
engines and their own engine commands. On a Dynamo cluster they write exactly
that and it runs on Dynamo — no Dynamo-specific manifest, no reworked engine.
That gives a fleet one portable API across every stack it runs, and turning a
cluster over to Dynamo costs a developer nothing to learn. It's the same
native-engine experience Dynamo is already pursuing with the sidecar (§4).

### The property we want to keep

Modelplane's `ModelDeployment` describes a topology as a configuration of
engines. The contract reduces to one thing: **the engine is opaque** to
Modelplane. The user writes one container named `engine` with its `image`,
`command`, and `args`, and what they write is what runs. We inject no engine
flags — no `--nnodes`, no `--node-rank`, no `--tensor-parallel-size`. The user
owns all of it.

This is why a new engine, or a new parallelism strategy, ships tomorrow and just
works: we never learn its flags, so we never need a release to support them. A
multi-node gang is a distinct `Leader` member and `Worker` member, each with its
own `command`, and the asymmetry between running the head and joining it lives
in the two commands the user writes — not in anything Modelplane derives.

The DGD as it stands today can't preserve that property, for the reasons in §4.
Closing that gap — with two Dynamo features, one already planned — is what lets
Modelplane compose a DGD behind an unchanged developer API.

## 3. First step: à la carte on an opt-in Dynamo cluster

We don't have to wait for the DGD to start. Most of Dynamo's near-term advantage
for us — topology-aware placement and ModelExpress — comes from projects that
work against a stock engine: [Grove](https://github.com/ai-dynamo/grove) and
[kai-scheduler](https://github.com/NVIDIA/KAI-Scheduler) for gang scheduling, and
[ModelExpress](https://github.com/ai-dynamo/modelexpress) for weight streaming.
Modelplane can adopt these under the model it already has, keeping the
`Standalone`/`Leader`/`Worker` engines it composes and the entire ML-facing API.

### The cluster opt-in

A cluster runs one serving stack, set on its `InferenceCluster`: `Standard`
(today's Modelplane-composed stack) or `Dynamo`, which turns on the à la carte
components below now and the composed DGD from §2 as it lands. Because the choice
is per-cluster, a fleet can take on Dynamo incrementally — standing up Dynamo
clusters and shifting deployments onto them cluster by cluster — rather than all
at once.

```yaml
apiVersion: modelplane.ai/v1alpha1
kind: InferenceCluster
metadata:
  name: eks-nvl72-us-east
spec:
  # The serving stack this cluster runs — the layer that plumbs a stock engine
  # into a routable serving instance. One per cluster:
  #
  #   Standard — Modelplane composes the workload itself (a Deployment or a
  #     LeaderWorkerSet) and fronts it with Gateway API + an endpoint picker.
  #   Dynamo   — Modelplane uses Dynamo's components: Grove + kai-scheduler for
  #     multi-node, ModelExpress for weight distribution (and, as it lands, a
  #     composed DGD). Installs the Dynamo platform (operator + NATS + Grove +
  #     kai-scheduler).
  #
  # One per cluster, so a fleet can take on Dynamo cluster by cluster.
  stack: Dynamo
  cluster:
    source: EKS
    eks:
      region: us-east-1
  nodePools:
  - name: gpu
    className: gb200-nvl72
```

### Grove and kai-scheduler

On a Dynamo cluster, Grove and kai-scheduler are the multi-node backend, with no
`ModelDeployment` change. A `Standalone` engine stays a Deployment; a
`Leader`+`Worker` gang becomes a Grove `PodCliqueSet` with a leader clique and a
worker clique, each carrying its own command, gang-scheduled by kai.
`compose-model-replica` composes the `PodCliqueSet` in place of the
LeaderWorkerSet and still injects `MODELPLANE_LEADER_ADDRESS`, now computed from
Grove's deterministic pod DNS instead of LWS's. Grove's per-clique pod specs map
onto our separate Leader and Worker commands, which a DGD's single pod template
can't (see §4). kai gang-schedules the group all-or-nothing and places it against
the interconnect topology.

### ModelExpress

On a Dynamo cluster the ModelExpress server runs as part of the stack, over
Modelplane's existing cache PVC, so it's the weight-distribution path available
to every engine there.

An engine opts in via its command. The user installs the ModelExpress client and
loads with `--load-format modelexpress`. Modelplane provides the server and the
`MX_*` env to reach it.

Because the user writes the loader flag and the engine is opaque (§2), the
command only runs where ModelExpress is — not on a `Standard` cluster. We don't
have a great answer to that coupling yet;
[modelplaneai/modelplane#379](https://github.com/modelplaneai/modelplane/issues/379)
explores one, where Modelplane computes the right loader flags per replica and
exposes them as an env var the user interpolates. In the meantime an all-Dynamo
fleet avoids it entirely, and a mixed fleet can steer these deployments with a
`clusterSelector` (a `modelexpress: true` or `cache-type: modelexpress` label)
so they land only where the loader will work.

```yaml
apiVersion: modelplane.ai/v1alpha1
kind: ModelDeployment
metadata:
  name: qwen3-8b
  namespace: ml-team
spec:
  replicas: 4
  template:
    spec:
      modelCacheRef:
        name: qwen3-8b          # the PVC and ModelExpress server come from this cache on a Dynamo cluster
      engines:
      - name: qwen3-8b
        members:
        - role: Standalone
          nodeSelector:
            devices:
            - name: gpu
              count: 1
              selectors:
              - cel: device.driver == "gpu.nvidia.com"
            - name: efa                   # optional: fast fabric for P2P; without it, TCP or a storage read
              count: 1
              selectors:
              - cel: device.driver == "dra.net"
          template:
            spec:
              containers:
              - name: engine
                # The user writes the ModelExpress opt-in; Modelplane injects nothing into
                # command/args. It runs the server and supplies the MX_* env to reach it.
                image: vllm/vllm-openai:v0.23.0
                command: ["/bin/sh", "-c"]
                args:
                - >-
                  pip install --no-deps modelexpress &&
                  exec vllm serve Qwen/Qwen3-8B --load-format modelexpress
```

Behind the `modelCacheRef`, Modelplane provisions the RWX PVC as it does today,
runs a `modelexpress-server` over it (reachable through its own Service;
Modelplane injects `MX_SERVER_ADDRESS`), and pre-caches the repo into the PVC
through the server. The server coordinates through ModelExpress's Kubernetes CRD
backend.

ModelExpress's registry dedups the pull and serves it offline, so there's no
cold-deploy stampede on HuggingFace. Modelplane binds an RDMA NIC if the engine
requests one. At run time the first replica reads the PVC, registers its VRAM,
and publishes its worker endpoint as a `ModelMetadata` record. Every later
replica reads that record and pulls the weights from it directly, pod to pod,
rather than re-reading storage.

The fast NIC is optional — a performance choice, not a requirement. On EKS it's
EFA, modeled as a claimable DRA device on the `InferenceClass` (driver `dra.net`,
via DraNet); an InfiniBand fabric would be a `Synthetic` placement-only device
instead. Either gives NIXL — ModelExpress's transfer layer — a fabric for the
fast path. Without one NIXL still moves weights peer-to-peer over TCP, and with
no peer at all a replica reads the PVC.

```yaml
apiVersion: modelplane.ai/v1alpha1
kind: InferenceClass
metadata:
  name: gb200-nvl72
spec:
  devices:
  - name: gpu
    claim: DRA
    driver: gpu.nvidia.com
    deviceClassName: gpu.nvidia.com
    count: 8
  # Optional fast fabric for ModelExpress's peer-to-peer transfer. On EKS that's
  # EFA, a claimable DRA device via DraNet (driver dra.net); an InfiniBand fabric
  # would be a Synthetic placement-only device instead. Without either, NIXL
  # falls back to TCP.
  - name: efa
    claim: DRA
    driver: dra.net
    deviceClassName: efa.networking.k8s.aws
    count: 16
```

### On GMS and Snapshot

We propose we defer both the GPU Memory Service and Snapshot for now. They're
capabilities we want (§1), but neither is cleanly à la carte: consuming them
would mean Modelplane rebuilding the operator machinery Dynamo already ships
around them. GMS needs a controller that watches for failed engine pods and
force-deletes them without touching their GMS pods. Snapshot needs a privileged,
node-level DaemonSet plus operator wiring — a checkpoint CRD, a mutating webhook,
a placeholder image — and is still experimental. Rather than rebuild and run
that ourselves, we'll pick both up with the DGD, where they come as part of the
platform.

## 4. Asks to reach the end goal

To get from §3 to §2 (a DGD composed behind an unchanged `ModelDeployment`) we
have two asks. One is already on Dynamo's roadmap (the sidecar); one isn't
(distinct leader and worker components). Together they're what lets a
`ModelDeployment` land on a Dynamo cluster with the same engines, the same
flags, and the same per-role commands it would carry on any other cluster —
preserving the opaque-engine property from §2.

### Where the DGD breaks the contract today

**One template, roles inferred.** A multi-node DGD component is one `type:
worker` component with `multinode.nodeCount: N` and a single `podTemplate`. The
operator expands it into a leader clique and a worker clique, and there's nowhere
to put a distinct leader command or worker command. So a replica whose engine is
`Leader`+`Worker` — two members, two commands — can't map onto a DGD without us
throwing the worker command away or fighting the operator's launch generation.

**The operator rewrites the command.** On multi-node the operator appends
distributed-launch flags to the user's `args`: `backend_vllm.go` injects
`--distributed-executor-backend mp --nnodes N --master-addr <leader>
--master-port 29500`, leader adds `--node-rank 0`, workers add `--node-rank
<rank> --headless`; SGLang gets `--dist-init-addr`, `--nnodes`, `--node-rank`;
TRT-LLM gets wrapped in `mpirun`. The user's command is not the command that
runs.

Both of these only fire when a *single* component is multinode. The injection
logic in `backend_vllm.go` short-circuits on `numberOfNodes <= 1`
(`shouldInjectVLLMMpWaitLeaderInit`, `updateVLLMMultinodeArgs`); a single-node
component is pass-through, the command you write is what runs.

**And the engine runs in a Dynamo runtime image.** A DGD worker launches
`python3 -m dynamo.<backend>`, not `vllm serve`; the engine runs inside a Dynamo
Python module in a `*-runtime` image. Ask 1 already targets this.

### Ask 1: Replace the Python runtime frontend with a sidecar

Per [ai-dynamo/dynamo#10835](https://github.com/ai-dynamo/dynamo/issues/10835),
Dynamo already intends to support vanilla upstream engine images (e.g. vLLM) by
moving its bespoke logic from the runtime wrapper (`python3 -m dynamo.<backend>`)
to a sidecar container:

> Operator preference. We see signals that users prefer each engine's native
> `xxx serve` experience over a Dynamo-specific `dynamo.xxx` entrypoint, largely
> because CLI behavior and options diverge.

An API sketch of a DGD using the sidecar would help us here, along with a sense
of how the sidecar affects what flags and environment variables the engines
need.

### Ask 2: Distinct leader and worker components

We'd like an advanced, opt-in mode in which the DGD author can specify leader
and worker engine pod specs separately. In this mode the DGD controller wouldn't
inject any engine flags. It would pass through what the user specifies, as
Modelplane does. We believe DGD `v1beta1` could add this as an optional feature
without breaking changes. For example:

```yaml
components:
- name: worker-leader
  type: worker-leader  # Needs a better name, open to ideas.
  replicas: 1
  podTemplate:
    spec:
      containers:
      - name: main
        image: vllm/vllm-openai:v0.23.0
        command:
        - /bin/sh
        - -c
        - >-
          exec vllm serve /mnt/models
          --served-model-name=qwen3-coder
          --tensor-parallel-size=8
          --pipeline-parallel-size=2
          --distributed-executor-backend=mp
          --nnodes=2 --node-rank=0
          --master-addr=$DYN_LEADER_ADDRESS
          --max-model-len=32768
          --port=8000
- name: worker-follower
  type: worker-follower  # Better name needed here too.
  replicas: 1
  podTemplate:
    spec:
      containers:
      - name: main
        image: vllm/vllm-openai:v0.23.0
        command:
        - /bin/sh
        - -c
        - >-
          exec vllm serve /mnt/models
          --served-model-name=qwen3-coder
          --tensor-parallel-size=8
          --pipeline-parallel-size=2
          --distributed-executor-backend=mp
          --nnodes=2 --node-rank=1
          --master-addr=$DYN_LEADER_ADDRESS
          --headless
          --max-model-len=32768
```

We'd want Dynamo to set env vars inside the leader and follower pods — for
example the `$DYN_LEADER_ADDRESS` referenced above — so the two can discover each
other, the way `LWS_LEADER_ADDRESS` and `GROVE_HEADLESS_SERVICE` already do.

## 5. Alternative considered: a delegated engine

Before the plan above, we considered exposing the DGD directly in Modelplane's
API rather than composing it invisibly. In that design the ML team hands a whole
engine to Dynamo through a new member `role: Delegated` and a `stack: Dynamo`
selector, writing a Dynamo runtime image and letting Dynamo synthesize the
multi-node launch:

```yaml
engines:
- name: llama-405b
  copies: 1
  members:
  - role: Delegated          # NEW: hand the engine to a serving stack's orchestrator
    stack: Dynamo
    dynamo:
      nodes: 2               # total gang size; the stack owns the multi-node launch
    template:
      spec:
        containers:
        - name: engine
          image: nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.2.1
          command: ["python3", "-m", "dynamo.vllm"]
          args:
          - --model=meta-llama/Llama-3.1-405B-Instruct
          - --tensor-parallel-size=8
          - --pipeline-parallel-size=2
```

This maps cleanly onto the DGD as it exists today because it accepts the DGD's
single-template, roles-inferred model and its runtime image. Modelplane would
compose a single DGD (a synthesized frontend plus one component per engine) and
point the replica's route at Dynamo's frontend.

We're not proposing this as the goal, for two reasons. It changes the ML-facing
API: `Delegated` is a new role and a Dynamo-specific block, so the user's
`ModelDeployment` now looks different depending on which cluster it lands on.
And it breaks the opaque-engine contract from §2: the engine must be a
`dynamo.<backend>` runtime image rather than the stock `vllm serve` the user
writes everywhere else. The §2 end goal reaches the same DGD backend while
keeping the API and the engine identical across clusters.
