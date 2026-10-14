"""In-cluster deployment — contract tests for the kustomize manifests (#2).

These tests pin the manifests STATICALLY: they parse the YAML under
``manifests/`` and assert the deployment contract — the rendered objects are
the behavior under test. No cluster is needed; they fail on a missing or
non-conforming file.

End-to-end acceptance (``oc apply -k`` on a dev cluster, patrol completes,
wiki persists across pod restarts) is a documented procedure —
.ai/spec/what/in-cluster-deployment.md rule 17 — not part of this suite.

Contract: .ai/spec/what/in-cluster-deployment.md [PLANNED: #2].

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

# Secret-sourced env keys (rule 10) — never literal in committed manifests.
SECRET_ENV_KEYS = {
    "PATROL_API_KEY",
    "PATROL_BASE_URL",
    "PATROL_MODEL",
    "SLACK_WEBHOOK_URL",
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
    allowed = EXPECTED_RESOURCES | {"kustomization.yaml", "secret.yaml.example"}
    names = {p.name for p in MANIFESTS.glob("*") if p.is_file()}
    assert names <= allowed, (
        f"manifests/ may contain only kustomization.yaml, the six resources "
        f"and the secret.yaml.example template (rule 1), got extra: "
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


def test_args_loop_interval_wiki():
    args = [str(a) for a in _container().get("args", [])]
    assert args == ["--loop", "--interval", "900",
                    "--wiki", "/wiki/cluster-wiki.md"], (
        f"args must be exactly --loop --interval 900 --wiki "
        f"/wiki/cluster-wiki.md (rule 9), got: {args}"
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
    for key in ("SLACK_WEBHOOK_URL", "PATROL_WIKI_REMOTE"):
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
