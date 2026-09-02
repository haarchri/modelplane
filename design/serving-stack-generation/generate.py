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

"""Generate per-cloud serving stack component lists from AICR recipes.

Prototype for the generator serving-stack-generation.md describes, and
the stand-in for `nix run .#stacks`. It writes one Python module per
cloud into stacks/generated/aicr/, each a COMPONENTS list of Chart and
Manifests entries in the shape compose-serving-stack iterates. In-tree
those files land in functions/compose-serving-stack/function/stacks/
generated/aicr/ and join the hand-written common.py and the stack's own
file; here they sit beside the design as evidence the pipeline works.

Per cloud, one `aicr recipe` at Modelplane's coordinate (service +
intent, os only where a recipe covers Modelplane's node image - see the
design's "Resolving Modelplane's coordinate") and one `aicr bundle`
with Modelplane's managed values forced via --set. The accelerator is
held out of the coordinate, so the generator also resolves once per
covered accelerator and unions the Kubernetes constraint floors,
failing if the strictest outruns the cloud cluster XRD's default.

GKE needs Modelplane's catalog fork. The `gpuStack` profile locks the
GPU-advertiser choice at every output boundary (0.20.0 also closed
the `bundle --set` path 0.18.0 let through), and neither upstream
value advertises through DRA. aicr's --data mechanism replaces
embedded catalog files wholesale by name, so catalog/overlays/
gke-cos.yaml carries a fork of the embedded overlay whose one change
is a third profile value, `dra`; the generator selects it with
--profile gpuStack=dra. The fork must be re-synced when the pinned
aicr moves. See the design's "What this needs".

Usage (needs `aicr` 0.20.0 on PATH):

    uv run --with pyyaml python3 generate.py [cloud ...]

With no arguments every cloud in CLOUDS regenerates. Classification
detail (drops, managed paths, dropped dependencies, manifests,
constraint floors) goes to stderr; the generated files carry
provenance in their header.

Every component in a recipe must be classified in ALLOW or DROP. An
unknown component fails the run: the alternative is NVIDIA adding a
component that silently appears in every Modelplane cluster on
regeneration.
"""

import ast
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "stacks" / "generated" / "aicr"

# Sentinel for "this path is not set at all" in lookups.
ABSENT = "<absent>"

# One generated module per cloud AICR has a `service` value for and
# Modelplane can bundle. `os` appears where a recipe covers the node
# image Modelplane uses: EKS leaves it unset because no recipe covers
# AL2023, and AKS refuses --os without an accelerator, so its cloud
# coordinate leaves it unset too. `accelerators` is the covered set the
# constraint-floor union resolves over; widening it is a line here and
# a regeneration. `k8s_default` is the cloud cluster XRD's default
# Kubernetes version - keep in sync with the XRDs. Nebius, Vultr, the
# AMD stack and Existing are hand-written files, not this generator's
# business; their prototypes sit at the top of stacks/ beside the
# generated ones.
CLOUDS = {
    "eks": {
        "os": None,
        "accelerators": ["h100", "h200", "gb200", "rtx-pro-6000"],
        "k8s_default": "1.36",
    },
    "aks": {
        "os": None,
        "accelerators": ["h100"],
        "k8s_default": "1.34",
    },
    "gke": {
        "os": "cos",
        "accelerators": ["h100", "b200"],
        # DRA on GKE needs Standard >= 1.35 (Google's set-up-dra guide) -
        # a floor AICR doesn't carry because neither stock gpuStack value
        # allocates through DRA, and one the fork can't reliably inject:
        # later overlays in the chain restate K8s.server.version, and a
        # profile value may not restate a composed-recipe constraint
        # name. So Modelplane owns it here, in the union.
        "floors": ["1.35"],
        "k8s_default": "1.35",
    },
}

# GKE resolves against Modelplane's catalog fork and its dra profile
# value (see module docstring). Applied to every GKE aicr recipe call,
# floor probes included, so the fork's union-totality and coherence
# checks run on each.
RECIPE_EXTRA = {
    "gke": ["--data", str(ROOT / "catalog"), "--profile", "gpuStack=dra"],
}

# aicr rejects its own wildcard toleration under AKS admission.
BUNDLE_EXTRA = {
    "aks": ["--accelerated-node-toleration", "nvidia.com/gpu=present:NoSchedule"],
}

# Recipe component name -> the key we emit it as. The key and the
# mp-prefixed release name are Modelplane's, not AICR's, so a
# component's identity survives upstream renames (see the design's
# "Ordering and identity").
ALLOW = {
    "nfd": "node-feature-discovery",
    "cert-manager": "cert-manager",
    "kube-prometheus-stack": "kube-prometheus-stack",
    "prometheus-operator-crds": "prometheus-operator-crds",
    "prometheus-adapter": "prometheus-adapter",
    "k8s-ephemeral-storage-metrics": "k8s-ephemeral-storage-metrics",
    "gpu-operator": "gpu-operator",
    "nvidia-dra-driver-gpu": "nvidia-dra-driver-gpu",
    "nvsentinel": "nvsentinel",
    "nodewright-operator": "nodewright-operator",
    "nodewright-customizations": "nodewright-customizations",
}

# In the recipe, deliberately not in the stack. Each entry needs a
# reason; an unclassified component fails the run. The allowlist fails
# closed.
DROP = {
    "agentgateway": "Modelplane routes through an InferencePool behind Envoy AI Gateway",
    "agentgateway-crds": "Modelplane routes through an InferencePool behind Envoy AI Gateway",
    "aws-efa": "the EKSCluster installs an EFA DRA driver when a pool asks for the fabric",
    "aws-ebs-csi-driver": "Modelplane's only storage need is RWX, which the EKSCluster serves from EFS",
    "network-operator": "the AKSCluster composition installs it when a pool asks for InfiniBand",
    "kai-scheduler": "contract surface, not hardware surface; Modelplane's pin beside its Queues, in stacks/dynamo.py",
}

# Long recipe-carried config blobs keep their content, not their
# cosmetics: dcgm-exporter's metrics CSV ships with blank spacer lines
# the exporter ignores. Trimming them keeps the generated file
# readable.
TRIM = {
    "gpu-operator": [("dcgmExporter", "config", "data")],
}

# Values Modelplane must own regardless of what a recipe says,
# mirroring the design's modelplane-*-inference overlay: DRA whole-GPU
# allocation over the device plugin (AICR rejects a partial flip), and
# the driver from the node image, which is Modelplane's mode on every
# cloud today ("The GPU driver" leans toward conforming to AICR's
# operator-installed default on EKS eventually; that lands here as a
# value change, not an API change). Turning the operator's driver off
# forces nvidiaDriverRoot to name where the node image put it, and
# forces NVSentinel's labeler to assume a driver it has no pod to
# observe - AICR's bundler refuses to render without both.
#
# This table is both applied and asserted: managed_sets() turns it
# into `aicr bundle --set` flags, and the transform verifies each
# value landed in the hydrated bundle. The assertion catches aicr
# silently ignoring a --set for a mistyped component key.
MANAGED = {
    "nvidia-dra-driver-gpu": [
        (("gpuResourcesEnabledOverride",), True, "Modelplane allocates GPUs via DRA ResourceSlices"),
        (("resources", "gpus", "enabled"), True, "Modelplane allocates GPUs via DRA ResourceSlices"),
        (("resources", "computeDomains", "enabled"), False, "multi-node NVLink unused"),
        (("nvidiaDriverRoot",), "/", "the node image puts the driver at the default root"),
    ],
    "gpu-operator": [
        (("driver", "enabled"), False, "the node image provides the kernel driver"),
        (("toolkit", "enabled"), False, "the node image provides the container toolkit"),
        (("devicePlugin", "enabled"), False, "the device plugin and DRA ResourceSlices double-advertise GPUs"),
        (("gdrcopy", "enabled"), False, "no operator-managed driver to build the kernel module against"),
    ],
    "nvsentinel": [
        (("labeler", "assumeDriverInstalled"), True, "no driver pod to observe with the operator's driver off"),
    ],
}

# Per-cloud expected values where a cloud legitimately differs.
MANAGED_CLOUD = {
    "gke": {
        # Google's installer puts the driver here under every gpuStack value.
        ("nvidia-dra-driver-gpu", ("nvidiaDriverRoot",)): "/home/kubernetes/bin/nvidia",
    },
}

# Paths the cloud's catalog data already sets - the gke fork's dra
# profile value owns the advertiser paths (aicr rejects a --set on a
# profile-owned path) and its overlay sets the driver root. Asserted in
# the hydrated bundle like every managed path, just not --set.
CATALOG_OWNED = {
    "gke": {
        ("nvidia-dra-driver-gpu", ("gpuResourcesEnabledOverride",)),
        ("nvidia-dra-driver-gpu", ("resources", "gpus", "enabled")),
        ("nvidia-dra-driver-gpu", ("nvidiaDriverRoot",)),
        ("gpu-operator", ("devicePlugin", "enabled")),
        ("nvsentinel", ("labeler", "assumeDriverInstalled")),
    },
}


def managed(cloud):
    """The MANAGED table with per-cloud expected values substituted."""
    out = {}
    for component, entries in MANAGED.items():
        out[component] = [
            (path, MANAGED_CLOUD.get(cloud, {}).get((component, path), value), why)
            for path, value, why in entries
        ]
    return out


def deep_merge(base, override):
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def lookup(values, path):
    """Read values[path], or ABSENT if any segment is missing."""
    node = values
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return ABSENT
        node = node[key]
    return node


def set_path(values, path, value):
    node = values
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def version_key(version):
    return tuple(int(x) for x in re.findall(r"\d+", version or "0"))


def managed_sets(cloud):
    """Render the managed table as `aicr bundle --set` flags."""
    flags = []
    owned = CATALOG_OWNED.get(cloud, set())
    for component, entries in managed(cloud).items():
        for path, value, _ in entries:
            if (component, path) in owned:
                continue
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            flags += ["--set", f"{component}:{'.'.join(path)}={rendered}"]
    return flags


def bundle_values(bundle_dir, name):
    """Load the hydrated values aicr bundle wrote for a component."""
    matches = sorted(bundle_dir.glob(f"[0-9][0-9][0-9]-{name}/values.yaml"))
    if not matches:
        return {}
    return yaml.safe_load(matches[0].read_text()) or {}


def k8s_floor(recipe):
    """A recipe's Kubernetes floor, and its other constraints verbatim."""
    floors, others = [], []
    for constraint in recipe.get("constraints") or []:
        name, value = constraint.get("name", ""), str(constraint.get("value", ""))
        if name == "K8s.server.version":
            match = re.search(r"\d+(?:\.\d+)*", value)
            if not match:
                sys.exit(f"unparseable Kubernetes constraint {value!r} - fail closed")
            floors.append(match.group(0))
        else:
            others.append(f"{name} {value}".strip())
    return floors, others


def run(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"{' '.join(argv)}\n{proc.stderr.strip()}")


def transform(cloud, recipe, bundle_dir):
    """Turn a hydrated recipe into Chart and Manifests entries."""
    findings = []
    components = []
    refs = {c["name"]: c for c in recipe["componentRefs"]}
    table = managed(cloud)
    owned = CATALOG_OWNED.get(cloud, set())

    for name in table:
        if name not in refs:
            findings.append(f"managed component {name} absent from the recipe")

    # AICR's install sequence is discarded as ordering (installs stay
    # concurrent; depends_on drives teardown) but kept as a stable
    # file order.
    for name in recipe["deploymentOrder"]:
        ref = refs[name]
        if name in DROP:
            findings.append(f"dropped: {name} ({DROP[name]})")
            continue
        if name not in ALLOW:
            sys.exit(f"unknown component {name!r}: classify it in ALLOW or DROP")
        key = ALLOW[name]

        values = deep_merge(bundle_values(bundle_dir, name), ref.get("overrides"))
        for path in TRIM.get(name, []):
            blob = lookup(values, path)
            if isinstance(blob, str):
                lines = [line.rstrip() for line in blob.splitlines()]
                set_path(values, path, "\n".join(line for line in lines if line) + "\n")
                findings.append(f"trimmed: {name}.{'.'.join(path)}: blank lines and trailing whitespace")
        for path, expected, why in table.get(name, []):
            dotted = f"{name}.{'.'.join(path)}"
            actual = lookup(values, path)
            if actual != expected:
                sys.exit(
                    f"managed path {dotted} is {actual!r}, expected "
                    f"{expected!r}: the value did not land in the bundle - fail closed"
                )
            via = "the catalog fork" if (name, path) in owned else "aicr bundle --set"
            findings.append(f"managed path: {dotted} = {expected} ({why}; via {via})")

        depends = []
        for dep in ref.get("dependencyRefs", []):
            if dep in ALLOW:
                depends.append(ALLOW[dep])
            elif dep in DROP:
                findings.append(
                    f"dropped dep: {name} -> {dep} (dropped; the join with "
                    "the hand-written files must satisfy it or nothing needed it)"
                )
            else:
                sys.exit(f"dependency {name} -> {dep!r} names an unclassified component - fail closed")

        entry = {
            "type": "Chart",
            "key": key,
            # provider-helm upgrades in place only while a release name
            # holds still; mp- reserves a namespace (see the design).
            "release": f"mp-{ref['chart']}",
            "namespace": ref["namespace"],
            "chart": ref["chart"],
            "repository": ref["source"],
            "version": ref["version"],
        }
        if depends:
            entry["depends_on"] = depends
        if values:
            entry["values"] = values
        components.append(entry)

        # Components are not always just charts: on AKS the
        # gpu-operator carries a toolkit-hardening manifest. The bundle
        # materializes them under <NNN>-<name>-post/templates/, and
        # they render as provider-kubernetes Objects.
        if ref.get("manifestFiles"):
            rendered = sorted(bundle_dir.glob(f"[0-9][0-9][0-9]-{name}-post/templates/*.yaml"))
            if not rendered:
                sys.exit(f"component {name!r} carries manifestFiles the bundle did not render - fail closed")
            manifests = []
            for f in rendered:
                manifests.extend(d for d in yaml.safe_load_all(f.read_text()) if d)
            findings.append(f"manifests: {name}: {len(manifests)} objects ({', '.join(f.name for f in rendered)})")
            components.append({
                "type": "Manifests",
                "key": f"{key}-manifests",
                "depends_on": [key],
                "manifests": manifests,
            })

    return components, findings


def floors(cloud, spec, base_recipe, workdir):
    """Union the Kubernetes floors over the covered accelerators.

    The cloud coordinate holds the accelerator out, which drops the
    accelerator's constraints - the strictest. Resolve once per covered
    accelerator, union the floors, and fail if the strictest outruns
    the cloud cluster XRD's default.
    """
    findings = []
    united, others = k8s_floor(base_recipe)
    for floor in spec.get("floors", []):
        united.append(floor)
        findings.append(f"Modelplane-owned floor {floor} (see the CLOUDS table)")
    for other in others:
        findings.append(f"constraint (unenforced here): {other}")
    for accelerator in spec["accelerators"]:
        recipe_file = workdir / f"{cloud}-{accelerator}-floor.yaml"
        flags = ["--service", cloud, "--accelerator", accelerator, "--intent", "inference"]
        if spec["os"]:
            flags += ["--os", spec["os"]]
        run(["aicr", "recipe", *flags, *RECIPE_EXTRA.get(cloud, []), "-o", str(recipe_file)])
        got, others = k8s_floor(yaml.safe_load(recipe_file.read_text()))
        united += got
        for other in others:
            findings.append(f"constraint on {accelerator} (unenforced here): {other}")

    floor = max(united, key=version_key, default=None)
    if floor is None:
        findings.append("no Kubernetes floor asserted by any covered recipe")
        return None, findings
    if version_key(floor) > version_key(spec["k8s_default"]):
        sys.exit(
            f"{cloud}: strictest Kubernetes floor {floor} outruns the "
            f"cloud cluster XRD default {spec['k8s_default']} - fail closed"
        )
    findings.append(
        f"Kubernetes floor {floor} (union over no-accelerator, "
        f"{', '.join(spec['accelerators'])}); XRD default {spec['k8s_default']}"
    )
    return floor, findings


def pyfmt(value, indent):
    """Render a value as a Python literal, nested dicts one key per line."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = ["{"]
        for k, v in value.items():
            lines.append(f"{pad}    {k!r}: {pyfmt(v, indent + 4)},")
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = ["["]
        for item in value:
            lines.append(f"{pad}    {pyfmt(item, indent + 4)},")
        lines.append(pad + "]")
        return "\n".join(lines)
    if isinstance(value, str) and "\n" in value and '"""' not in value and not value.endswith('"'):
        return f'"""{value}"""'
    return repr(value)


def emit(cloud, spec, components, recipe, digest, floor):
    fields = ["key", "release", "namespace", "chart", "repository", "version", "depends_on", "values", "manifests"]
    lines = [
        f"# Generated by generate.py from aicr {recipe['metadata']['version']}. Do not edit.",
        "#",
        f"# Stand-in for functions/compose-serving-stack/function/stacks/generated/aicr/{cloud}.py;",
        "# in-tree, Chart and Manifests come from the stacks package, and the",
        "# function joins this list with the hand-written common.py and the stack's file.",
        "#",
        f"# Recipe: {' -> '.join(recipe['metadata']['appliedOverlays'])} (sha256:{digest[:12]})",
        f"# Kubernetes floor: {floor or 'none asserted'}; {cloud} XRD default {spec['k8s_default']}",
        "",
        "COMPONENTS: list[Component] = [",
    ]
    for entry in components:
        lines.append(f"    {entry['type']}(")
        for field in fields:
            if field not in entry:
                continue
            lines.append(f"        {field}={pyfmt(entry[field], 8)},")
        lines.append("    ),")
    lines += ["]", ""]
    text = "\n".join(lines)
    ast.parse(text)  # a generated file that doesn't parse fails the build
    (OUT / f"{cloud}.py").write_text(text)


def regenerate(cloud, workdir):
    spec = CLOUDS[cloud]
    flags = ["--service", cloud, "--intent", "inference"]
    if spec["os"]:
        flags += ["--os", spec["os"]]
    flags += RECIPE_EXTRA.get(cloud, [])
    recipe_file = workdir / f"{cloud}-inference.yaml"
    bundle_dir = workdir / f"{cloud}-bundle"

    run(["aicr", "recipe", *flags, "-o", str(recipe_file)])
    run([
        "aicr", "bundle", "--recipe", str(recipe_file), "--output", str(bundle_dir),
        *managed_sets(cloud), *BUNDLE_EXTRA.get(cloud, []),
    ])

    recipe = yaml.safe_load(recipe_file.read_text())
    components, findings = transform(cloud, recipe, bundle_dir)
    floor, floor_findings = floors(cloud, spec, recipe, workdir)
    for finding in findings + floor_findings:
        print(f"[{cloud}] {finding}", file=sys.stderr)

    digest = hashlib.sha256(recipe_file.read_bytes()).hexdigest()
    emit(cloud, spec, components, recipe, digest, floor)


def main(clouds):
    unknown = [c for c in clouds if c not in CLOUDS]
    if unknown:
        sys.exit(f"unknown clouds {unknown}; known: {', '.join(CLOUDS)}")
    if not shutil.which("aicr"):
        sys.exit("aicr not found on PATH")
    OUT.mkdir(parents=True, exist_ok=True)
    # Recipes and bundles are intermediates: reproducible from the
    # pinned aicr's embedded catalog, digested into the file headers,
    # and not worth checking in.
    with tempfile.TemporaryDirectory(prefix="serving-stack-gen-") as tmp:
        for cloud in clouds or CLOUDS:
            regenerate(cloud, pathlib.Path(tmp))
            print(f"generated stacks/generated/aicr/{cloud}.py")


if __name__ == "__main__":
    main(sys.argv[1:])
