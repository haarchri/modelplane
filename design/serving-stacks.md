# Serving stacks: Standard and Dynamo

**Status:** Accepted
**Date:** August 2026
**Author:** Nic Cope
**Issue:** [#111](https://github.com/modelplaneai/modelplane/issues/111)

## Summary

Modelplane composes and operates a serving layer across a fleet, but it isn't a
serving layer itself. Modelplane places, scales, caches, and fronts a model
across clusters and clouds. A serving stack owns what happens inside one
cluster, turning a stock engine into a routable serving instance. I propose we
add support for NVIDIA Dynamo as a serving stack.

The proposal is phased, so we deliver value now rather than waiting on one large
integration:

1. **Now — à la carte.** A platform team opts a cluster into a `Dynamo` serving
   stack, and Modelplane drives stock engines with Dynamo's standalone
   components: Grove + kai-scheduler for multi-node placement, ModelExpress for
   weight distribution. No change to the ML-facing API.
2. **In parallel — the gaps.** Two changes Dynamo already has in flight let a
   `ModelDeployment` land on a full `DynamoGraphDeployment` (DGD) without
   changing that API or what the engine runs.
3. **Then — the DGD.** The same `Dynamo` cluster opt-in graduates to composing
   full DGDs, so Modelplane's users get Dynamo's frontend and routing while the
   API they write stays exactly what it is today.

## Background

**Topology-aware placement and multi-role startup ordering.** Where a model's
shards land relative to the interconnect (NVLink domain → rack → zone) sets the
throughput it can reach. Modelplane has no model of that hierarchy today and
doesn't gang-schedule, so a large deployment can land split across domains or
stall half-placed. Grove and kai-scheduler offer topology-aware, all-or-nothing
placement.

**Faster cold starts (ModelExpress).** When Modelplane scales a deployment up,
each new replica loads its weights from storage. This is the slowest part of
coming online, and a source of contention when many replicas start at once.
ModelExpress lets a scaling-up replica skip that read entirely.

**Checkpoint/restore cold starts (Snapshot).** Bringing an engine online means
building the CUDA context, compiling kernels, and loading weights before it can
serve. Snapshot restores a checkpoint of a fully initialized worker instead,
cutting that startup to a restore.

**GPU-memory sharing and failover (GMS).** Modelplane has no fast-recovery story
today. When an engine crashes, its replacement reloads weights from disk. GMS
keeps a model's weights resident so a restarting or standby engine re-attaches
instead of reloading.

**Mid-stream request migration.** Today, if a worker dies mid-generation, the
best Modelplane can do is retry the whole request, and a long in-flight
generation is lost. Dynamo's frontend replays the delivered tokens to a new
worker and continues the stream.

Topology-aware placement and ModelExpress work against a stock, unmodified
engine, so Modelplane can adopt them without waiting for the DGD. Mid-stream
migration needs Dynamo's frontend-and-wrapped-engine architecture, which for us
means a composed DGD. GMS and Snapshot could work à la carte too, but we'd
rather get them from a DGD than rebuild their supporting machinery ourselves.

### Why now

Modelplane ran two serving stacks once.
[#15](https://github.com/modelplaneai/modelplane/pull/15) added Dynamo alongside
the KServe stack it already had, installing Dynamo's operator, etcd, and NATS and
composing a DynamoGraphDeployment.
[#44](https://github.com/modelplaneai/modelplane/pull/44) removed it a month
later, and [design.md](./design.md) records why under Multiple inference
orchestrators: a model's serving profile named the stack it wanted, so the API
could only hold what both supported, and that surface shrinks with each stack
added. [#99](https://github.com/modelplaneai/modelplane/pull/99) then dropped
KServe too, leaving Modelplane to compose the serving layer itself, the stack
this proposal calls Standard.

What's changed since is the shape of the API. [Unopinionated
ModelDeployments](./unopinionated-deployments.md) stopped describing a stack's
configuration and started describing an engine: `Standalone`, or a `Leader` and a
`Worker`, each with a command Modelplane passes through untouched. A
`ModelDeployment` names no stack, and Modelplane derives no engine flags from it.

That turns the question around. Instead of asking what every stack has in common,
we ask whether a given one can run what a `ModelDeployment` already describes:
two pod specs, two commands, and a way for a worker to find its leader. Grove and
kai-scheduler can, which is why the first phase needs no API change. A DGD can't
today, though the two additions below would be enough. KServe couldn't without
changing a good deal about KServe.

## Goals

**Give Modelplane users Dynamo capabilities.** Topology-aware gang scheduling
and faster cold starts now; failover, checkpoint restore, and mid-stream
migration as they follow. Adopting a serving stack that has them beats building
each one ourselves.

**No ML-facing API change.** A `ModelDeployment` reads the same whichever stack
it lands on. That gives a fleet one portable API, and turning a cluster over to
Dynamo costs a developer nothing to learn.

**Keep the engine opaque.** The user writes one container named `engine` with
its `image`, `command`, and `args`, and what they write is what runs. We inject
no engine flags.

**Choose per cluster.** A fleet stands up Dynamo clusters and shifts deployments
onto them one at a time.

## Proposal

[Grove](https://github.com/ai-dynamo/grove) and
[kai-scheduler](https://github.com/NVIDIA/KAI-Scheduler) gang-schedule multi-node
engines, and [ModelExpress](https://github.com/ai-dynamo/modelexpress) streams
weights between them. All three work against a stock engine, so Modelplane can
adopt them under the model it already has.

### The cluster opt-in

A cluster runs one serving stack, set on its `InferenceCluster`: `Standard`
(today's Modelplane-composed stack) or `Dynamo`, which turns on the à la carte
components below now and the composed DGD as it lands. Because the choice is
per-cluster, a fleet can take on Dynamo incrementally, standing up Dynamo
clusters and shifting deployments onto them cluster by cluster.

```yaml
apiVersion: modelplane.ai/v1alpha1
kind: InferenceCluster
metadata:
  name: eks-nvl72-us-east
spec:
  # The serving stack this cluster runs — the layer that plumbs a stock engine
  # into a routable serving instance. One per cluster:
  #
  #   Standard — Modelplane composes a Deployment or a LeaderWorkerSet and
  #     fronts it with Gateway API + an endpoint picker.
  #   Dynamo   — Modelplane uses Dynamo's components: Grove + kai-scheduler for
  #     multi-node, ModelExpress for weight distribution (and, as it lands, a
  #     composed DGD). Installs the Dynamo platform (operator + NATS + Grove +
  #     kai-scheduler).
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

On a Dynamo cluster, Grove and kai-scheduler are the multi-node backend. A
`Standalone` engine stays a Deployment; a `Leader`+`Worker` gang becomes a Grove
`PodCliqueSet` with a leader clique and a worker clique. Each carries its own
command, and is gang-scheduled all-or-nothing by kai. `compose-model-replica`
composes the `PodCliqueSet` in place of the LeaderWorkerSet. Grove's per-clique
pod specs map onto our separate Leader and Worker commands, which a DGD's single
pod template can't.

The gang's coordination env comes across unchanged, so a command reads the same
on either stack. `MODELPLANE_LEADER_ADDRESS` aliases Grove's deterministic pod
DNS rather than LWS's, and `MODELPLANE_RANK` aliases the gang-wide pod index
[grove#755](https://github.com/ai-dynamo/grove/pull/755) adds.

### ModelExpress

ModelExpress does two things: it runs a cache service that downloads and
deduplicates model weights, and it moves weights GPU-to-GPU between replicas over
NIXL. I propose we take only the second.

A ModelCache stays what it is on either stack, a per-cache PVC that Modelplane
hydrates itself. A Dynamo cluster additionally runs one ModelExpress server,
which brokers which replica holds a model in GPU memory and never handles the
weight bytes. The first replica loads from the PVC and publishes itself as a
source; later replicas pull from a peer's GPU. Modelplane injects the `MX_*` env
into every engine that references a cache.

Letting ModelExpress own the cache instead — one shared PVC it hydrates through
its own registry — would make a ModelCache mean something different depending on
which cluster it lands on, and put its weights out of reach of any engine not
using ModelExpress's loader. Keeping the PVC ours costs us the deduplicated pull
and buys a cache, and a deployment, that are portable across both stacks.

An engine opts in through its command: install the client, load with
`--load-format modelexpress`. Modelplane injects no `--load-format` of its own.
The command stays portable even so, because ModelExpress falls back to the
engine's native loader when it finds no server, no peer, or no fabric. On a
`Standard` cluster it just loads from the PVC.

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
        name: qwen3-8b          # a plain PVC on either stack; on Dynamo its replicas also seed each other
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

Behind the `modelCacheRef`, Modelplane provisions the RWX PVC and hydrates it as
it does on either stack, and binds an RDMA NIC if the engine requests one. The
cluster's one `modelexpress-server` is reachable through its own Service and
coordinates through ModelExpress's Kubernetes CRD backend; Modelplane injects
`MX_SERVER_ADDRESS`. A replica registers its VRAM and publishes its worker
endpoint as a `ModelMetadata` record for its peers to find.

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
  # Optional fast fabric for ModelExpress's peer-to-peer transfer.
  - name: efa
    claim: DRA
    driver: dra.net
    deviceClassName: efa.networking.k8s.aws
    count: 16
```

## Future improvements

### A composed DGD

Nothing about the cluster opt-in changes: the same `stack: Dynamo` graduates from
composing components to composing a `DynamoGraphDeployment`, and an ML developer
writes the `Standalone`/`Leader`/`Worker` engines they'd write anywhere else.

A DGD can't preserve the opaque engine today, for three reasons. A multi-node
component has one `podTemplate` that the operator expands into a leader and a
worker, so there's nowhere to put a distinct worker command. The operator then
appends its own launch flags to whatever the user wrote: `backend_vllm.go`
injects `--nnodes`, `--node-rank`, `--master-addr` and `--headless`, SGLang gets
`--dist-init-addr`, and TRT-LLM is wrapped in `mpirun`. And the engine runs as
`python3 -m dynamo.<backend>` in a Dynamo runtime image rather than as `vllm
serve`.

Dynamo is working on all three.
[#10835](https://github.com/ai-dynamo/dynamo/issues/10835) moves the runtime
wrapper's logic into a sidecar, so a stock engine image serves:

> Operator preference. We see signals that users prefer each engine's native
> `xxx serve` experience over a Dynamo-specific `dynamo.xxx` entrypoint, largely
> because CLI behavior and options diverge.

For the other two we need a worker's pod spec to be able to differ from its
leader's, and a way to tell the operator not to generate launch flags, leaving
the commands the user wrote. Dynamo is working on both in
[#12696](https://github.com/ai-dynamo/dynamo/issues/12696), whose motivation
names our case: integrations "that provide different Leader and Worker commands,
environment, DRA references, or placement."

## Alternatives considered

### A gang scheduler on the Standard stack

Gang scheduling and topology-aware placement are the one capability above with
credible alternatives. Volcano and Kueue can both gang-schedule a LeaderWorkerSet
against a hardware topology, and LWS integrates with each. JobSet can express
cross-role startup ordering.

So we could close that gap without Dynamo, by adding Volcano to the Standard
stack. A per-cluster stack means Standard can gain a gang scheduler without
touching a Dynamo cluster, and a fleet runs whichever fits each cluster.

That closes one of the gaps above and leaves the rest, and the ones it leaves are
the ones with no alternative at all. Nothing outside Dynamo keeps a model's
weights resident in GPU memory across an engine crash, and nothing short of a
frontend that owns the stream can resume a generation mid-flight. On a Dynamo
cluster gang scheduling arrives with those.

### A delegated engine

Before the plan above, I considered exposing the DGD directly in Modelplane's
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

I'm not proposing it, because it gives up both goals at once. `Delegated` is a
new role and a Dynamo-specific block, so a `ModelDeployment` reads differently
depending on which cluster it lands on. And the engine must be a
`dynamo.<backend>` runtime image rather than the stock `vllm serve` the user
writes everywhere else. The end state above reaches the same DGD backend without
giving up either.
