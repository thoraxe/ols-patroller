# OLS Patroller — in-cluster image (#1).
# Contract: .ai/spec/what/container-image.md (rules 1-10).

# Rule 1: ubi9-minimal base; python + git (the wiki repo needs git,
# wiki-memory.md rules 4a-4e) installed via microdnf. tar/gzip are build-time
# extraction tools for the pinned client tarball below — the minimal base
# ships neither.
FROM registry.access.redhat.com/ubi9/ubi-minimal:9.8

RUN microdnf install -y python3.12 python3.12-pip git tar gzip \
    && microdnf clean all

# Rules 2-4: OpenShift client (oc AND kubectl) fetched at build time from a
# version-pinned mirror URL (bumping the client = editing this literal), its
# SHA-256 verified BEFORE extraction, all in one RUN: fetch -> verify -> extract.
RUN curl -fsSL -o /tmp/openshift-client-linux.tar.gz \
      https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/4.22.18/openshift-client-linux.tar.gz \
    && echo "07751676809553e012448cdb3a755ffa7b11d7e25b8a6cc60a5a62a9f4b18159  /tmp/openshift-client-linux.tar.gz" \
       | sha256sum -c - \
    && tar -xzf /tmp/openshift-client-linux.tar.gz -C /usr/local/bin oc kubectl \
    && rm -f /tmp/openshift-client-linux.tar.gz

WORKDIR /app

# Rules 5-6: Python dependencies installed at build time from the exactly
# pinned repo-root requirements.txt — nothing resolves or installs at start.
COPY requirements.txt ./
RUN python3.12 -m pip install --no-cache-dir -r requirements.txt

COPY patrol.py ./

# Rule 8: /wiki mount point (the PVC from #2) group-owned by GID 0 with group
# write, so the random UIDs OpenShift SCCs assign can write it.
RUN mkdir -p /wiki && chgrp 0 /wiki && chmod g=u /wiki

# Rule 8: non-root, random-UID compatible (arbitrary UID, primary GID 0);
# git needs no HOME — patrol.py passes user identity per command.
USER 1001:0

# Rule 9: default patrol — loop every 6 h (21600 s), wiki on the /wiki volume.
# Rule 10: no ENV — PATROL_* / SLACK_* arrive at runtime from the #2 Secret.
ENTRYPOINT ["python3.12", "patrol.py"]
CMD ["--loop", "--interval", "21600", "--wiki", "/wiki/cluster-wiki.md"]
