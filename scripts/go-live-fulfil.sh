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

step "Restart the engine, so it mounts the new Secret, then fulfil a missing auto need from its page."
echo "   ssh root@sustenaut k3s kubectl -n convergence rollout restart deployment/engine"
echo "   Then on https://convergence.sustenaut.eu, a service with a missing k8s.secret or bitwarden.generated need: Fulfil."
