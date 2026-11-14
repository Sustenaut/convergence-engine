#!/usr/bin/env bash
# Once, from David's laptop: the steps before the engine's Fulfil button works (step 4b).
#
#   scripts/go-live-fulfil.sh
#
# Each step says what it does and asks first; a step already done is answered "n".
# It prints no secret: the write token is typed into Bitwarden's own page, and the
# walk moves it to the cluster from memory.
set -euo pipefail
cd "$(dirname "$0")/.."

n=0
step() { n=$((n + 1)); printf '\n%d. %s\n' "$n" "$1"; }
ask() { local a; read -r -p "   $1 [y/N] " a; [[ "$a" == y ]]; }

step "The GitHub App \"Sustenaut convergence\" may write: actions, secrets, variables and environments (github-app.json)."
echo "   On https://github.com/organizations/Sustenaut/settings/apps, the App's Permissions: set those four to Read and write,"
echo "   save, then accept the new permissions on the installation. The rounds still mint a token that may only read."
ask "Done?" || { echo "go-live: the App's permissions first, then run this again"; exit 1; }

step "The engine's Bitwarden write token, stored in Sustenaut as convergence-bitwarden-write-token."
echo "   In Bitwarden Secrets Manager: a new access token on the engine's write machine account (read and write on"
echo "   Sustenaut only), pasted into a new secret convergence-bitwarden-write-token in Sustenaut."
ask "Done?" || { echo "go-live: the write token first, then run this again"; exit 1; }

step "The walk applies k8s/admin/write-role.yaml (the write role and its admission policy) and fills Secret convergence-bitwarden-write."
if ask "Run ./with-secrets ./pubdeploy walk --fulfil --server sustenaut now?"; then
  [[ -f ../shared-infra/pubkit.json ]] || { echo "go-live: ../shared-infra is not checked out beside this repository" >&2; exit 1; }
  ./with-secrets ./pubdeploy walk --fulfil --server sustenaut --zones ../shared-infra/pubkit.json
fi

step "Prove the admission policy: the engine's writes are refused in kube-system and allowed in a service's namespace."
if ask "Check over ssh root@sustenaut (server-side dry runs, which write nothing)?"; then
  # shellcheck disable=SC2029  # the arguments are ours, expanded here on purpose
  k() { ssh root@sustenaut k3s kubectl "$@"; }
  k api-resources --api-group=admissionregistration.k8s.io -o name | grep -qx validatingadmissionpolicies.admissionregistration.k8s.io \
    || { echo "go-live: this cluster has no ValidatingAdmissionPolicy (Kubernetes 1.30 or later): the policy does not hold" >&2; exit 1; }
  as=(--as=system:serviceaccount:convergence:app)
  probe='{"apiVersion":"v1","kind":"Secret","metadata":{"name":"convergence-probe"}}'
  if denied=$(echo "$probe" | k "${as[@]}" -n kube-system create --dry-run=server -f - 2>&1); then
    echo "go-live: the engine could create a Secret in kube-system: the policy does not hold" >&2; exit 1
  fi
  grep -q "the convergence engine writes nothing in the system namespaces" <<<"$denied" \
    || { echo "go-live: kube-system refused the engine, but not by the policy: $denied" >&2; exit 1; }
  echo "$probe" | k "${as[@]}" -n notifier create --dry-run=server -f - >/dev/null \
    || { echo "go-live: the engine may not create a Secret in notifier: the write role is not applied" >&2; exit 1; }
  echo "   kube-system refused by the policy; notifier allowed."
fi

step "Restart the engine, so it mounts the new Secret."
if ask "Run ssh root@sustenaut k3s kubectl -n convergence rollout restart deployment/engine now?"; then
  ssh root@sustenaut k3s kubectl -n convergence rollout restart deployment/engine
  ssh root@sustenaut k3s kubectl -n convergence rollout status deployment/engine --timeout=180s
fi
echo "   Then on https://convergence.sustenaut.eu, a service with a missing k8s.secret or bitwarden.generated need: Fulfil."
