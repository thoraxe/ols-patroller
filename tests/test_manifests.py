"""In-cluster deployment — contract tests for the kustomize manifests (#2).

These tests pin the manifests STATICALLY: they parse the YAML under
``manifests/`` and assert the deployment contract — the rendered objects are
the behavior under test. No cluster is needed; they fail on a missing or
non-conforming file.

End-to-end acceptance (``oc apply -k`` on a dev cluster, patrol completes,
wiki persists across pod restarts) is a documented procedure —
.ai/spec/what/in-cluster-deployment.md rule 17 — not part of this suite.

Contract: .ai/spec/what/in-cluster-deployment.md [PLANNED: #2]
[deploy-key wiring: rules 16a-16d, PLANNED: #39]
[pull policy: rule 8a, PLANNED: #61]
[storage-class override: rule 5a, PLANNED: #68].

Run: uv run --group dev pytest tests/ -q
"""
import re
import subprocess
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parent.parent
MANIFESTS = REPO / "manifests"
KUSTOMIZATION = MANIFESTS / "kustomization.yaml"

NAMESPACE = "ols-patroller"
PVC_NAME = "ols-patroller-wiki"
IMAGE_KEY = "ols-patroller"
DEPLOY_KEY_SECRET = "ols-patroller-deploy-key"
DEPLOY_KEY_PATCH_EXAMPLE = MANIFESTS / "deploy-key-patch.yaml.example"

# Secret-sourced env keys (rule 10) — never literal in committed manifests.
SECRET_ENV_KEYS = {
    "PATROL_API_KEY",
    "PATROL_BASE_URL",
    "PATROL_MODEL",
    "PATROL_NOTIFY_URLS",
    "PATROL_WIKI_REMOTE",
}

# Kustomization resources (rule 1) — everything the directory may contain
# besides the secret.yaml.example template (which is not a resource).
EXPECTED_RESOURCES = {
    "namespace.yaml",
    "serviceaccount.yaml",
    "clusterrolebinding.yaml",
    "pvc.yaml",
    "secret.yaml",
    "deployment.yaml",
}

# Only RBAC object allowed anywhere in the manifests (rule 4): the CRB.
ALLOWED_KINDS = {
    "Kustomization",
    "Namespace",
    "ServiceAccount",
    "ClusterRoleBinding",
    "PersistentVolumeClaim",
    "Secret",
    "Deployment",
}


# ------------------------------------------------------------------ helpers

def _load(path: Path) -> dict:
    assert path.exists(), (
        f"{path.relative_to(REPO)} must exist "
        f"(.ai/spec/what/in-cluster-deployment.md, #2)"
    )
    return yaml.safe_load(path.read_text())


def _kustomization() -> dict:
    doc = _load(KUSTOMIZATION)
    assert doc.get("kind") == "Kustomization", (
        "manifests/kustomization.yaml must be a Kustomization (rule 1)"
    )
    return doc


def _deployment() -> dict:
    doc = _load(MANIFESTS / "deployment.yaml")
    assert doc.get("kind") == "Deployment"
    assert doc["metadata"]["name"] == NAMESPACE, (
        "the Deployment must be named ols-patroller — rule 17's acceptance "
        "procedure keys on deploy/ols-patroller (rule 7)"
    )
    return doc


def _pod_spec() -> dict:
    return _deployment()["spec"]["template"]["spec"]


def _container() -> dict:
    containers = _pod_spec().get("containers", [])
    assert containers, "the Deployment must have a container (rule 8)"
    return containers[0]


# ------------------------------------------------------------------ layout & apply (rules 1-2)

def test_kustomization_lists_exactly_the_expected_resources():
    resources = _kustomization().get("resources")
    assert resources, (
        "kustomization.yaml must list its resources (rule 1)"
    )
    assert set(resources) == EXPECTED_RESOURCES, (
        f"kustomization resources must be exactly {sorted(EXPECTED_RESOURCES)} "
        f"(rule 1), got: {sorted(resources)}"
    )


def test_committed_manifest_files_exist():
    # secret.yaml is gitignored (rule 14) — everything else is committed.
    for name in sorted(EXPECTED_RESOURCES - {"secret.yaml"}):
        _load(MANIFESTS / name)


def test_kustomization_sets_no_namespace_transformer():
    k = _kustomization()
    assert "namespace" not in k, (
        "the kustomization must not set the namespace transformer — it does "
        "not rewrite ClusterRoleBinding subjects; namespaces are per-file "
        "literals (rule 2)"
    )


def test_no_extra_files_in_manifests_dir():
    assert MANIFESTS.exists(), (
        "manifests/ must exist (.ai/spec/what/in-cluster-deployment.md, #2)"
    )
    allowed = EXPECTED_RESOURCES | {
        "kustomization.yaml",
        "secret.yaml.example",
        # #39: the deploy-key patch template (committed, rule 16a) and the
        # operator's gitignored copy (rule 16c) — templates/copies, not
        # resources.
        "deploy-key-patch.yaml.example",
        "deploy-key-patch.yaml",
        # #68: the storage-class patch template (committed, rule 5a) and the
        # operator's gitignored copy — templates/copies, not resources.
        "pvc-patch.yaml.example",
        "pvc-patch.yaml",
    }
    names = {p.name for p in MANIFESTS.glob("*") if p.is_file()}
    assert names <= allowed, (
        f"manifests/ may contain only kustomization.yaml, the six resources "
        f"and the .example templates, plus the gitignored operator copies "
        f"(secret.yaml, deploy-key-patch.yaml, pvc-patch.yaml) (rule 1), got extra: "
        f"{sorted(names - allowed)}"
    )


# ------------------------------------------------------------------ identity & RBAC (rules 3-4)

def test_namespace_object():
    doc = _load(MANIFESTS / "namespace.yaml")
    assert doc.get("kind") == "Namespace"
    assert doc["metadata"]["name"] == NAMESPACE, (
        f"the Namespace must be named {NAMESPACE!r} (rule 2)"
    )


def test_all_namespaced_objects_pin_the_same_namespace():
    for name in ("serviceaccount.yaml", "pvc.yaml", "deployment.yaml"):
        doc = _load(MANIFESTS / name)
        assert doc["metadata"].get("namespace") == NAMESPACE, (
            f"{name} must carry the literal namespace {NAMESPACE!r} (rule 2)"
        )
    crb = _load(MANIFESTS / "clusterrolebinding.yaml")
    subjects = crb.get("subjects") or []
    assert subjects, "the ClusterRoleBinding must have a subject (rule 4)"
    assert subjects[0].get("namespace") == NAMESPACE, (
        "the ClusterRoleBinding subject namespace must match (rule 2)"
    )


def test_serviceaccount_referenced_by_deployment():
    sa = _load(MANIFESTS / "serviceaccount.yaml")
    assert sa.get("kind") == "ServiceAccount"
    assert sa["metadata"]["name"] == NAMESPACE
    ps = _pod_spec()
    assert ps.get("serviceAccountName") == NAMESPACE, (
        "the pod template must run as the ols-patroller ServiceAccount (rule 3)"
    )
    assert ps.get("automountServiceAccountToken") is not False, (
        "the SA token must automount — it is how the image's oc authenticates "
        "in-cluster (rule 3)"
    )


def test_crb_binds_sa_to_builtin_cluster_reader():
    crb = _load(MANIFESTS / "clusterrolebinding.yaml")
    assert crb.get("kind") == "ClusterRoleBinding"
    ref = crb.get("roleRef") or {}
    assert ref.get("apiGroup") == "rbac.authorization.k8s.io"
    assert ref.get("kind") == "ClusterRole"
    assert ref.get("name") == "cluster-reader", (
        "the SA must bind to the BUILT-IN cluster-reader ClusterRole (rule 4, "
        "ADR 0006) — no bespoke role"
    )
    assert crb.get("subjects") == [
        {"kind": "ServiceAccount", "name": NAMESPACE, "namespace": NAMESPACE}
    ], (
        f"exactly one subject — the {NAMESPACE} SA (rule 4)"
    )


def test_no_bespoke_rbac_objects():
    assert MANIFESTS.exists(), (
        "manifests/ must exist (.ai/spec/what/in-cluster-deployment.md, #2)"
    )
    for path in MANIFESTS.glob("*.yaml"):
        doc = yaml.safe_load(path.read_text())
        assert doc.get("kind") in ALLOWED_KINDS, (
            f"{path.name}: unexpected kind {doc.get('kind')!r} — the only "
            f"RBAC object is the single ClusterRoleBinding (rule 4)"
        )


# ------------------------------------------------------------------ persistence (rules 5-6)

def test_pvc_rwo_1gi_default_storage_class():
    pvc = _load(MANIFESTS / "pvc.yaml")
    assert pvc.get("kind") == "PersistentVolumeClaim"
    assert pvc["metadata"]["name"] == PVC_NAME
    spec = pvc["spec"]
    assert spec.get("accessModes") == ["ReadWriteOnce"], (
        "the wiki PVC must be ReadWriteOnce (rule 5)"
    )
    assert spec.get("resources", {}).get("requests", {}).get("storage") == "1Gi", (
        "the wiki PVC must request 1Gi (rule 5)"
    )
    assert "storageClassName" not in spec, (
        "the wiki PVC must use the cluster's default storage class (rule 5)"
    )


def test_deployment_mounts_wiki_pvc_at_wiki():
    mounts = _container().get("volumeMounts", [])
    m = next((m for m in mounts if m.get("mountPath") == "/wiki"), None)
    assert m, "the patrol container must mount a volume at /wiki (rule 5)"
    assert m.get("name") == "wiki", (
        "the /wiki mount's volume must be named 'wiki' (rule 5)"
    )
    vols = _pod_spec().get("volumes", [])
    v = next((v for v in vols if v.get("name") == m["name"]), None)
    assert v, f"volume {m['name']!r} must be defined in the pod spec (rule 5)"
    assert v.get("persistentVolumeClaim", {}).get("claimName") == PVC_NAME, (
        f"the /wiki mount must be the {PVC_NAME} PVC (rule 5)"
    )


def test_pod_security_context_scc_supplied_identity():
    sc = _pod_spec().get("securityContext") or {}
    assert sc.get("runAsNonRoot") is True, "runAsNonRoot must be true (rule 6)"
    assert sc.get("seccompProfile", {}).get("type") == "RuntimeDefault", (
        "seccompProfile must be RuntimeDefault (rule 11)"
    )
    assert set(sc) == {"runAsNonRoot", "seccompProfile"}, (
        f"the pod securityContext must contain nothing else (rule 11) — the "
        f"SCC supplies identity and the fsGroup that makes /wiki writable "
        f"(rule 6, ADR 0007); got extra: {sorted(set(sc) - {'runAsNonRoot', 'seccompProfile'})}"
    )


# ------------------------------------------------------------------ the deployment (rules 7-13)

def test_single_replica_recreate():
    spec = _deployment()["spec"]
    assert spec.get("replicas") == 1, "exactly one replica (rule 7)"
    assert spec.get("strategy", {}).get("type") == "Recreate", (
        "strategy must be Recreate — a RollingUpdate surge pod can never "
        "mount the RWO volume (rule 7)"
    )


def test_image_key_and_kustomize_override():
    assert _container().get("image") == IMAGE_KEY, (
        f"the container image must be the literal key {IMAGE_KEY!r} — no "
        f"registry baked into deployment.yaml (rule 8)"
    )
    images = _kustomization().get("images") or []
    entry = next((e for e in images if e.get("name") == IMAGE_KEY), None)
    assert entry, (
        "the kustomization must carry the images override entry (rule 8)"
    )
    assert entry.get("newName") == "ghcr.io/thoraxe/ols-patroller", (
        "the images override must map to the CI-published GHCR image "
        "(rule 8, container-image.md rules 13-16)"
    )
    assert entry.get("newTag") == "main", (
        "the images override must default to the rolling :main tag (rule 8)"
    )


def test_container_pulls_always_for_the_rolling_tag():
    # Rule 8a (#61): the deployed tag is rolling (:main) — under the
    # k8s-default IfNotPresent, every rollout restart on a node with the
    # tag cached is a silent no-op that keeps the stale image.
    assert _container().get("imagePullPolicy") == "Always", (
        "the patrol container must set imagePullPolicy: Always while the "
        "kustomization tag is rolling (:main) — IfNotPresent makes every "
        "rollout restart silently keep the cached stale image (rule 8a, #61)"
    )


def test_mutable_image_tag_requires_always_pull_policy():
    # Rule 8a (#61), the invariant rather than the current value: whenever
    # the kustomization images tag is mutable (not a sha-… pin, not a
    # digest), the container must pull Always. Pinning the tag is the
    # other legal form — an operator who pins may drop the policy.
    entry = next((e for e in (_kustomization().get("images") or [])
                  if e.get("name") == IMAGE_KEY), None)
    assert entry, "the kustomization must carry the images override (rule 8)"
    tag = str(entry.get("newTag") or "")
    digest = str(entry.get("digest") or "")
    pinned = bool(digest) or tag.startswith("sha-") or tag.startswith("sha256:")
    if not pinned:
        assert _container().get("imagePullPolicy") == "Always", (
            f"the kustomization tag {tag!r} is mutable — the container must "
            f"set imagePullPolicy: Always (rule 8a, #61), or the tag must "
            f"be pinned to sha-…/digest"
        )


def test_args_loop_interval_wiki():
    args = [str(a) for a in _container().get("args", [])]
    assert args == ["--loop", "--interval", "21600",
                    "--wiki", "/wiki/cluster-wiki.md"], (
        f"args must be exactly --loop --interval 21600 --wiki "
        f"/wiki/cluster-wiki.md — 6h cadence (rule 9, #25), got: {args}"
    )


def test_env_from_secret_no_literal_secrets():
    c = _container()
    assert c.get("envFrom") == [{"secretRef": {"name": NAMESPACE}}], (
        "PATROL_* secrets must arrive via envFrom secretRef on the "
        "ols-patroller Secret (rule 10)"
    )
    for e in c.get("env", []):
        if e.get("name") in SECRET_ENV_KEYS:
            assert "value" not in e, (
                f"{e['name']} must never be a literal env value (rule 10)"
            )
    cid = next((e for e in c.get("env", []) if e.get("name") == "PATROL_CLUSTER_ID"),
               None)
    assert cid and cid.get("value") and cid["value"] != "local", (
        "PATROL_CLUSTER_ID must be a literal env the operator edits to the "
        "real cluster name — never left at the 'local' default (rule 10)"
    )


def test_unbuffered_stdout_env():
    # Rule 10a (#24): stdout is block-buffered in a container (no TTY) —
    # without PYTHONUNBUFFERED the findings/notification block can sit in
    # the stdio buffer while `oc logs` shows nothing mid-patrol.
    e = next((e for e in _container().get("env", [])
              if e.get("name") == "PYTHONUNBUFFERED"), None)
    assert e is not None and e.get("value") == "1", (
        "PYTHONUNBUFFERED must be a literal env entry with value '1' "
        "(rule 10a, #24) — pod logs otherwise stay empty mid-patrol"
    )


def test_wiki_git_safe_directory_env():
    # Rule 10b (#28): the PVC mount point is root-owned, so git's
    # CVE-2022-24765 ownership check rejects /wiki for every non-root
    # caller — the GIT_CONFIG_* trio makes the repo safe for any
    # in-container git (rule 17 step 4's `oc exec` procedure included).
    env = {e.get("name"): e.get("value") for e in _container().get("env", [])}
    assert env.get("GIT_CONFIG_COUNT") == "1", (
        "GIT_CONFIG_COUNT must be the literal '1' (rule 10b, #28)"
    )
    assert env.get("GIT_CONFIG_KEY_0") == "safe.directory", (
        "GIT_CONFIG_KEY_0 must be 'safe.directory' (rule 10b, #28)"
    )
    assert env.get("GIT_CONFIG_VALUE_0") == "/wiki", (
        "GIT_CONFIG_VALUE_0 must be '/wiki' (rule 10b, #28)"
    )


def test_container_security_context():
    sc = _container().get("securityContext") or {}
    assert sc.get("allowPrivilegeEscalation") is False, (
        "allowPrivilegeEscalation must be false (rule 11)"
    )
    assert "ALL" in sc.get("capabilities", {}).get("drop", []), (
        "all capabilities must be dropped (rule 11)"
    )


def test_no_host_or_privileged_wiring():
    ps = _pod_spec()
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        assert not ps.get(key), f"{key} must not be set (rule 11)"
    for v in ps.get("volumes", []):
        assert "hostPath" not in v, "hostPath volumes are forbidden (rule 11)"
    assert "imagePullSecrets" not in ps, (
        "the base manifests assume the public GHCR package — no "
        "imagePullSecrets (Constraints)"
    )
    assert not _container().get("privileged"), (
        "the container must not be privileged (rule 11)"
    )


def test_resources_requests_and_limits():
    r = _container().get("resources") or {}
    quantity = re.compile(r"^\d+(\.\d+)?(m|Ki|Mi|Gi|Ti|k|M|G|T)?$")
    for section in ("requests", "limits"):
        for key in ("cpu", "memory"):
            v = (r.get(section) or {}).get(key)
            assert v is not None and quantity.match(str(v)), (
                f"resources.{section}.{key} must be set to a valid quantity "
                f"(rule 12), got: {v!r}"
            )
    assert r.get("requests") == {"cpu": "100m", "memory": "256Mi"}, (
        "default requests are cpu 100m / memory 256Mi (rule 12)"
    )
    assert r.get("limits") == {"cpu": "1", "memory": "512Mi"}, (
        "default limits are cpu 1 / memory 512Mi (rule 12)"
    )


def test_selector_matches_template_labels():
    spec = _deployment()["spec"]
    selector = spec.get("selector", {}).get("matchLabels") or {}
    labels = spec.get("template", {}).get("metadata", {}).get("labels") or {}
    assert selector, "the Deployment must have a selector (rule 13)"
    assert selector.get("app") == NAMESPACE, (
        "the selector must key on app: ols-patroller (rule 13)"
    )
    for k, v in selector.items():
        assert labels.get(k) == v, (
            f"pod template label {k}={v!r} must match the selector (rule 13)"
        )


# ------------------------------------------------------------------ the secret (rules 14-15)

def test_secret_template_placeholders():
    example = MANIFESTS / "secret.yaml.example"
    assert example.exists(), (
        "manifests/secret.yaml.example must be committed as the Secret "
        "template (rule 14)"
    )
    raw = example.read_text()
    doc = yaml.safe_load(raw)
    assert doc.get("kind") == "Secret"
    assert doc["metadata"]["name"] == NAMESPACE
    assert doc["metadata"].get("namespace") == NAMESPACE, (
        "the Secret template must carry the literal namespace (rule 2)"
    )
    string_data = doc.get("stringData") or {}
    for key in ("PATROL_API_KEY", "PATROL_BASE_URL", "PATROL_MODEL"):
        assert key in string_data, (
            f"the Secret template must carry required key {key} (rule 14)"
        )
        assert re.match(r"^<.+>$", str(string_data[key])), (
            f"{key} in the template must be an obvious <placeholder> value "
            f"(rule 14), got: {string_data[key]!r}"
        )
    for key in ("PATROL_NOTIFY_URLS", "PATROL_WIKI_REMOTE"):
        assert re.search(rf"^\s*#\s*{key}\s*:", raw, re.M), (
            f"optional key {key} must be documented as a commented-out line "
            f"in the template (rule 14)"
        )


def test_real_secret_is_gitignored():
    gitignore = (REPO / ".gitignore").read_text()
    assert re.search(r"^manifests/secret\.yaml\s*$", gitignore, re.M), (
        ".gitignore must ignore manifests/secret.yaml — real credentials are "
        "never committed (rule 14)"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "manifests/"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "manifests/secret.yaml" not in tracked, (
        "manifests/secret.yaml must never be tracked (rule 14)"
    )


def test_no_secret_values_in_committed_manifests():
    # Rule 15 scopes the check to COMMITTED manifests: the gitignored,
    # operator-filled manifests/secret.yaml is expected to contain the keys.
    tracked = subprocess.run(
        ["git", "ls-files", "manifests/"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked, (
        "manifests/ must exist with its files committed "
        "(.ai/spec/what/in-cluster-deployment.md rule 1, #2)"
    )
    for name in tracked:
        if name.endswith(".yaml") and name != "manifests/secret.yaml.example":
            text = (REPO / name).read_text()
            for key in SECRET_ENV_KEYS:
                assert key not in text, (
                    f"{key} must not appear outside the Secret template "
                    f"(rule 15), found in {name}"
                )


# ------------------------------------------------------------------ deploy key (rule 16)

def test_base_has_no_deploy_key_dependency():
    _load(MANIFESTS / "deployment.yaml")  # asserts existence with the right message
    text = (MANIFESTS / "deployment.yaml").read_text()
    for needle in ("deploy-key", "GIT_SSH_COMMAND", "id_ed25519", "id_rsa"):
        assert needle not in text, (
            f"the base Deployment must not reference deploy-key wiring "
            f"({needle!r}) — oc apply -k stays self-contained (rule 16)"
        )


# ------------------------------------------------- deploy key wiring (rules 16a-16d) [#39]


def _deploy_key_patch() -> dict:
    assert DEPLOY_KEY_PATCH_EXAMPLE.exists(), (
        "manifests/deploy-key-patch.yaml.example must be committed as the "
        "deploy-key patch template (rule 16a, #39)"
    )
    return yaml.safe_load(DEPLOY_KEY_PATCH_EXAMPLE.read_text())


def _deploy_key_pod_spec() -> dict:
    return _deploy_key_patch()["spec"]["template"]["spec"]


def test_deploy_key_patch_is_additive_partial_deployment():
    # Rule 16a (#39): a strategic-merge patch over the base Deployment —
    # additive only, carrying the base container's merge key and no image.
    doc = _deploy_key_patch()
    assert doc.get("apiVersion") == "apps/v1", "must target apps/v1 (rule 16a)"
    assert doc.get("kind") == "Deployment"
    assert doc["metadata"]["name"] == NAMESPACE, (
        "the patch must target the base Deployment by name (rule 16a)"
    )
    assert doc["metadata"]["namespace"] == NAMESPACE, (
        "the patch must carry the literal namespace (rule 2)"
    )
    assert set(doc["spec"]) == {"template"}, (
        "the patch must be additive — nothing at spec level beyond template "
        "(rule 16a)"
    )
    assert set(doc["spec"]["template"]) == {"spec"}
    ps = _deploy_key_pod_spec()
    assert set(ps) == {"containers", "volumes"}, (
        "the patch must touch only containers and volumes (rule 16a)"
    )
    c = ps["containers"][0]
    assert c["name"] == "ols-patroller", (
        "the patch must carry the base container's merge key 'ols-patroller' "
        "— strategic merge keys on name (rule 16a)"
    )
    assert set(c) == {"name", "env", "volumeMounts"}, (
        "the patch container entry must be name + env + volumeMounts only "
        "(rule 16a)"
    )
    assert "image" not in c, (
        "the patch must never carry the image key — it can never drift the "
        "kustomize image override (rule 16a)"
    )


def test_deploy_key_patch_mounts_secret_readonly_0440():
    # Rule 16a (#39): the deploy-key Secret volume, read-only, mode 0440 —
    # group-read for the arbitrary-UID GID-0 pod (the rule's rationale).
    ps = _deploy_key_pod_spec()
    vols = ps["volumes"]
    assert len(vols) == 1, "the patch adds exactly one volume (rule 16a)"
    v = vols[0]
    assert v["name"] == "deploy-key"
    assert v["secret"]["secretName"] == DEPLOY_KEY_SECRET, (
        f"the deploy-key volume must reference the {DEPLOY_KEY_SECRET} "
        f"Secret (rule 16a)"
    )
    assert v["secret"]["defaultMode"] == 0o440, (
        "defaultMode must be octal 0440 — group-read for the GID-0 pod; "
        "0400 would be root-only, 0644 needlessly world-readable (rule 16a)"
    )
    c = ps["containers"][0]
    mounts = c["volumeMounts"]
    assert len(mounts) == 1, "the patch adds exactly one mount (rule 16a)"
    assert mounts[0] == {
        "name": "deploy-key",
        "mountPath": "/wiki-deploy-key",
        "readOnly": True,
    }, "the deploy-key mount: /wiki-deploy-key, read-only (rule 16a)"


def test_deploy_key_patch_sets_git_ssh_command_tofu():
    # Rule 16a (#39): the one literal env entry, TOFU host-key posture.
    env = _deploy_key_pod_spec()["containers"][0]["env"]
    assert {e["name"] for e in env} == {"GIT_SSH_COMMAND"}, (
        "the patch's env must be exactly GIT_SSH_COMMAND — additive, never "
        "redefining base env entries (rule 16a)"
    )
    value = env[0]["value"]
    assert "-i /wiki-deploy-key/id_ed25519" in value, (
        "GIT_SSH_COMMAND must point at the mounted key (rule 16a)"
    )
    assert "StrictHostKeyChecking=accept-new" in value, (
        "the committed default is TOFU accept-new (rule 16a; the pinned "
        "variant is rule 16b)"
    )


def test_deploy_key_patch_documents_pinned_host_key_variant():
    # Rule 16b (#39): the fully pinned variant lives in the template as
    # commented lines.
    _deploy_key_patch()  # asserts the template exists with the right message
    raw = DEPLOY_KEY_PATCH_EXAMPLE.read_text()
    for needle in (
        "known_hosts",
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=/wiki-deploy-key/known_hosts",
    ):
        assert needle in raw, (
            f"the template must document the pinned host-key variant "
            f"({needle!r}, rule 16b)"
        )


def test_base_kustomization_has_no_patches_or_deploy_key():
    # Rule 16 (#39): the COMMITTED kustomization carries no patches entry —
    # the wiring is opt-in; the base apply stays self-contained. Scoped to
    # the HEAD blob, not the working tree: rule 16c step 4 edits the tracked
    # kustomization (it cannot be gitignored like secret.yaml / the patch
    # copy), and an operator who has opted in still sees the suite pass
    # locally — the contract governs what this repo commits.
    committed = subprocess.run(
        ["git", "show", "HEAD:manifests/kustomization.yaml"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout
    k = yaml.safe_load(committed)
    assert "patches" not in k, (
        "the committed kustomization must carry no patches: entry — the "
        "deploy-key patch is the operator's opt-in (rule 16)"
    )
    for needle in ("deploy-key", "GIT_SSH_COMMAND", "id_ed25519"):
        assert needle not in committed, (
            f"the base kustomization must not reference deploy-key wiring "
            f"({needle!r}) — oc apply -k stays self-contained (rule 16)"
        )


def test_deploy_key_patch_copy_is_gitignored():
    # Rule 16c (#39): the operator's copied-in patch is gitignored like
    # secret.yaml — real wiring never commits.
    gitignore = (REPO / ".gitignore").read_text()
    assert re.search(r"^manifests/deploy-key-patch\.yaml\s*$", gitignore, re.M), (
        ".gitignore must ignore manifests/deploy-key-patch.yaml — the "
        "operator's copy is never committed (rule 16c)"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "manifests/"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "manifests/deploy-key-patch.yaml" not in tracked, (
        "manifests/deploy-key-patch.yaml must never be tracked (rule 16c)"
    )


def test_secret_template_points_at_deploy_key_wiring():
    # Rule 14 (#39): the PATROL_WIKI_REMOTE placeholder shows the SSH form
    # and cross-references the patch template.
    raw = (MANIFESTS / "secret.yaml.example").read_text()
    line = next(
        (ln for ln in raw.splitlines()
         if re.match(r"^\s*#\s*PATROL_WIKI_REMOTE\s*:", ln)),
        "",
    )
    assert line, (
        "the template must document PATROL_WIKI_REMOTE as a commented-out "
        "key line (rule 14)"
    )
    assert "git@" in line, (
        "the PATROL_WIKI_REMOTE placeholder must show the SSH-remote form "
        "(rule 14, #39)"
    )
    assert "deploy-key-patch.yaml.example" in raw, (
        "the Secret template must point at the deploy-key patch template "
        "(rule 14, #39)"
    )


def test_readme_documents_deploy_key_procedure():
    # Rule 16d (#39): the README's in-cluster deployment section carries the
    # procedure end-to-end, self-sufficiently.
    readme = (REPO / "README.md").read_text()
    assert re.search(r"^## Deploy in-cluster\s*$", readme, re.M), (
        "README.md must have a 'Deploy in-cluster' section (rule 16d, #39)"
    )
    assert re.search(r"^### Remote wiki replication", readme, re.M), (
        "the README must carry the optional remote-replication subsection "
        "(rule 16d, #39)"
    )
    for needle in (
        "ssh-keygen",
        DEPLOY_KEY_SECRET,
        "deploy-key-patch.yaml.example",
        "patches:",
        "PATROL_WIKI_REMOTE",
        "git@",
    ):
        assert needle in readme, (
            f"the README deploy-key procedure must mention {needle!r} "
            f"(rule 16d, #39)"
        )


def test_deploy_key_patch_comments_state_0440_rationale():
    # Rule 16a (#39): the 0440/SCC rationale is stated in the template's
    # comments — the example teaches the operator, not just the machine.
    _deploy_key_patch()  # asserts the template exists with the right message
    raw = DEPLOY_KEY_PATCH_EXAMPLE.read_text()
    assert "0440" in raw, (
        "the template's comments must explain the 0440 mode (rule 16a)"
    )
    assert "root:root" in raw, (
        "the template's comments must state the root:root ownership of "
        "secret volume files (rule 16a)"
    )
    assert re.search(r"(?i)\bgid\b", raw), (
        "the template's comments must state the GID-0 mechanism (rule 16a)"
    )
    assert re.search(r"(?i)\buid\b", raw), (
        "the template's comments must state the arbitrary-UID mechanism "
        "(rule 16a)"
    )


def test_readme_deploy_section_is_self_sufficient():
    # Rule 16d (#39): the README section never references .ai/spec/ — an
    # operator enables replication from the README plus the committed
    # templates alone.
    readme = (REPO / "README.md").read_text()
    m = re.search(
        r"^## Deploy in-cluster\s*$\n(.*?)(?=^## |\Z)", readme, re.M | re.S
    )
    assert m, (
        "README.md must have a 'Deploy in-cluster' section (rule 16d, #39)"
    )
    assert ".ai/spec" not in m.group(1), (
        "the README deploy section must not reference .ai/spec/ — "
        "self-sufficient operator docs (rule 16d, #39)"
    )


def test_secret_env_keys_in_example_templates_are_comments_only():
    # Rule 15 (#39): secret env key names may appear in the deploy-key
    # template's comments, never as uncommented assignments with values.
    _deploy_key_patch()  # asserts the template exists with the right message
    raw = DEPLOY_KEY_PATCH_EXAMPLE.read_text()
    for ln in raw.splitlines():
        hit = next((k for k in SECRET_ENV_KEYS if k in ln), None)
        if hit:
            assert ln.lstrip().startswith("#"), (
                f"{hit} may appear in the .example template only in comments "
                f"(rule 15), got: {ln.strip()!r}"
            )


def test_pvc_patch_example_template():
    """Rule 5a (#68): the committed storage-class override template — a
    strategic-merge patch over the base PVC that sets exactly
    spec.storageClassName (never accessModes, size, or metadata beyond the
    merge key), so the patch can never drift the rest of the claim."""
    path = MANIFESTS / "pvc-patch.yaml.example"
    assert path.exists(), (
        "manifests/pvc-patch.yaml.example must exist (rule 5a, #68) — the "
        "committed template for the no-default-storage-class override"
    )
    doc = yaml.safe_load(path.read_text())
    assert doc["apiVersion"] == "v1", "the patch is a v1 PVC patch"
    assert doc["kind"] == "PersistentVolumeClaim"
    assert doc["metadata"]["name"] == PVC_NAME, "the patch keys on the base PVC"
    assert doc["metadata"]["namespace"] == NAMESPACE, (
        "rule 2: every namespaced object carries the literal namespace"
    )
    assert set(doc["spec"]) == {"storageClassName"}, (
        f"the patch must set exactly spec.storageClassName (rule 5a), "
        f"got {sorted(doc['spec'])}"
    )
    assert doc["spec"]["storageClassName"], "the class placeholder must be non-empty"


def test_pvc_patch_copy_is_gitignored():
    # Rule 5a (#68): the operator's copied-in patch is gitignored like
    # secret.yaml — real wiring never commits.
    gitignore = (REPO / ".gitignore").read_text()
    assert re.search(r"^manifests/pvc-patch\.yaml\s*$", gitignore, re.M), (
        ".gitignore must ignore manifests/pvc-patch.yaml — the operator's "
        "copy is never committed (rule 5a, #68)"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "manifests/"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "manifests/pvc-patch.yaml" not in tracked, (
        "manifests/pvc-patch.yaml must never be tracked (rule 5a, #68)"
    )


def test_readme_documents_pvc_patch_procedure():
    # Rule 5a (#68): the README's in-cluster deployment section carries the
    # storage-class override procedure end-to-end, self-sufficiently (the
    # rule-16d standard applied to the volume).
    readme = (REPO / "README.md").read_text()
    assert re.search(r"^### No default storage class", readme, re.M), (
        "the README must carry the 'No default storage class?' subsection "
        "(rule 5a, #68)"
    )
    for needle in (
        "oc get sc",
        "storageclass.kubernetes.io/is-default-class",
        "pvc-patch.yaml.example",
        "patches:",
        "storageClassName",
    ):
        assert needle in readme, (
            f"the README storage-class procedure must mention {needle!r} "
            f"(rule 5a, #68)"
        )
