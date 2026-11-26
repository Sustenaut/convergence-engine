# The thin image a live run's Job walks in (src/convergence/jobs.py): the tools a service's
# own ./pubdeploy and ./pubsec run, and nothing of the engine's. The Job checks the service
# out at the commit its run records and runs that commit's ./pubdeploy executor, which
# fetches its pinned wheels with uv, so the service's pins decide which pubdeploy and which
# providers walk it. Each tool is a pinned release, checked against its sha256 (the same
# pins as the engine's image), and terraform and psql for the needs that run them.
# Build from the repository's root:
#   podman build -f executor/Dockerfile -t convergence-executor .
FROM python:3.14-slim
# The source links the new package ghcr.io/sustenaut/convergence-executor to this repository on
# its first push, so this repository's workflow token may push it (without it GHCR denies it).
LABEL org.opencontainers.image.source="https://github.com/Sustenaut/convergence-engine" \
      org.opencontainers.image.description="The thin image a convergence engine live run's Job walks in." \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
ARG KUBECTL=v1.37.2 KUBECTL_SHA256=c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4
ARG KUSTOMIZE=5.8.3 KUSTOMIZE_SHA256=cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61
ARG GH=2.103.3 GH_SHA256=4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d
ARG BWS=2.1.0 BWS_SHA256=ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates unzip bind9-dnsutils \
      postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && cd /tmp \
    && curl --proto =https --tlsv1.2 -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL}/bin/linux/amd64/kubectl" \
    && echo "${KUBECTL_SHA256}  kubectl" | sha256sum -c - && install -m 755 kubectl /usr/local/bin/ \
    && curl --proto =https --tlsv1.2 -fsSLo kustomize.tgz "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE}/kustomize_v${KUSTOMIZE}_linux_amd64.tar.gz" \
    && echo "${KUSTOMIZE_SHA256}  kustomize.tgz" | sha256sum -c - && tar -xzf kustomize.tgz && install -m 755 kustomize /usr/local/bin/ \
    && curl --proto =https --tlsv1.2 -fsSLo gh.tgz "https://github.com/cli/cli/releases/download/v${GH}/gh_${GH}_linux_amd64.tar.gz" \
    && echo "${GH_SHA256}  gh.tgz" | sha256sum -c - && tar -xzf gh.tgz && install -m 755 "gh_${GH}_linux_amd64/bin/gh" /usr/local/bin/ \
    && curl --proto =https --tlsv1.2 -fsSLo bws.zip "https://github.com/bitwarden/sdk-sm/releases/download/bws-v${BWS}/bws-x86_64-unknown-linux-gnu-${BWS}.zip" \
    && echo "${BWS_SHA256}  bws.zip" | sha256sum -c - && unzip -q bws.zip && install -m 755 bws /usr/local/bin/ \
    && rm -rf /tmp/*
# HashiCorp's own image of terraform 1.17.2, by its digest.
COPY --from=hashicorp/terraform:1.17.2@sha256:6e9c1e647c31a960dc9b59e13272f9111d90010c10b717f77ffb324767660475 /bin/terraform /usr/local/bin/
COPY --from=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc /uv /uvx /usr/local/bin/
RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /work
