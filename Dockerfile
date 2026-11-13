# The convergence engine: the pages and the rounds, `uvicorn convergence.main:app`.
# Build from the repository's root:
#   podman build -t convergence-engine .
# It holds no credentials: the GitHub App's values, the storage token and the
# optional Bitwarden read token are mounted in the pod as files.
#
# A walk runs the walked repository's own ./pubdeploy (and ./pubsec), which fetch
# their pinned wheels with uv and curl, render with kustomize, read the cluster with
# kubectl as the pod's ServiceAccount, read GitHub with gh, and resolve DNS with dig (dns.site). Each tool is a pinned
# release, checked against its sha256; bws is there for ./pubsec run's reads.
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/opt/convergence
ARG KUBECTL=v1.37.2 KUBECTL_SHA256=c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4
ARG KUSTOMIZE=5.8.3 KUSTOMIZE_SHA256=cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61
ARG GH=2.103.3 GH_SHA256=4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d
ARG BWS=2.1.0 BWS_SHA256=ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates unzip bind9-dnsutils \
    && rm -rf /var/lib/apt/lists/* \
    && cd /tmp \
    && curl -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL}/bin/linux/amd64/kubectl" \
    && echo "${KUBECTL_SHA256}  kubectl" | sha256sum -c - && install -m 755 kubectl /usr/local/bin/ \
    && curl -fsSLo kustomize.tgz "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE}/kustomize_v${KUSTOMIZE}_linux_amd64.tar.gz" \
    && echo "${KUSTOMIZE_SHA256}  kustomize.tgz" | sha256sum -c - && tar -xzf kustomize.tgz && install -m 755 kustomize /usr/local/bin/ \
    && curl -fsSLo gh.tgz "https://github.com/cli/cli/releases/download/v${GH}/gh_${GH}_linux_amd64.tar.gz" \
    && echo "${GH_SHA256}  gh.tgz" | sha256sum -c - && tar -xzf gh.tgz && install -m 755 "gh_${GH}_linux_amd64/bin/gh" /usr/local/bin/ \
    && curl -fsSLo bws.zip "https://github.com/bitwarden/sdk-sm/releases/download/bws-v${BWS}/bws-x86_64-unknown-linux-gnu-${BWS}.zip" \
    && echo "${BWS_SHA256}  bws.zip" | sha256sum -c - && unzip -q bws.zip && install -m 755 bws /usr/local/bin/ \
    && rm -rf /tmp/*
COPY --from=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc /uv /uvx /usr/local/bin/
# From GitHub's releases and PyPI, and when either fails (GitHub's downloads 504 at
# times), from storage's mirrors of the same files: the lock's hashes decide either way.
COPY requirements.lock scripts/mirrored-lock.py /tmp/
RUN pip install --only-binary :all: --require-hashes -r /tmp/requirements.lock \
    || { python /tmp/mirrored-lock.py /tmp/requirements.lock > /tmp/mirrored.lock \
         && pip install --only-binary :all: --require-hashes --index-url https://storage.sustenaut.eu/pypi/simple \
              -r /tmp/mirrored.lock; } \
    && rm -f /tmp/requirements.lock /tmp/mirrored.lock /tmp/mirrored-lock.py
COPY src/convergence/ /opt/convergence/convergence/
RUN useradd --create-home --uid 1000 app
USER app
CMD ["uvicorn", "convergence.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
