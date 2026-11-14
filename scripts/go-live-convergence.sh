#!/usr/bin/env bash
# Once, from David's laptop: the steps before the engine's first walk of itself.
#
#   scripts/go-live-convergence.sh
#
# Each step says what it does and asks first; a step already done is answered "n".
# It stops on the first error and prints no secret: the GitHub App's key goes from
# GitHub to Bitwarden inside pubsec, and the read token is typed into Bitwarden's own
# page. After it, the deploy walk does the rest and says what is left.
set -euo pipefail
cd "$(dirname "$0")/.."

n=0
step() { n=$((n + 1)); printf '\n%d. %s\n' "$n" "$1"; }
ask() { local a; read -r -p "   $1 [y/N] " a; [[ "$a" == y ]]; }

step "The GitHub App \"Sustenaut convergence\", read-only (github-app.json), its three values to Bitwarden as convergence-app-id, convergence-app-key and convergence-installation-id."
if ask "Make it now with ./pubsec github-app create? It opens GitHub in the browser"; then
  ./pubsec github-app create github-app.json
fi

step "Install the App on every repository with the role deploy, on shared-infra (the zones) and on release-state (the list)."
echo "   On the App's installation page, choose those repositories. A service the App cannot reach shows as a failed clone on its page."
ask "Done?" || { echo "go-live: install the App first, then run this again"; exit 1; }

step "A read-only Bitwarden access token for the engine, stored in Sustenaut as convergence-bitwarden-read-token."
echo "   In Bitwarden Secrets Manager: a new access token on the read-only machine account (Claude Business OS),"
echo "   pasted into a new secret convergence-bitwarden-read-token in Sustenaut. Without it, the walks show bitwarden.* needs unknown."
ask "Done?" || echo "   Left for later: the engine walks without it."

step "The engine's first walk of itself, from this laptop over the admin channel: the namespace, the admin manifests, the Secrets, the sign-in client, the gate."
if ask "Run ./with-secrets ./pubdeploy walk --fulfil --server sustenaut now?"; then
  [[ -f ../shared-infra/pubkit.json ]] || { echo "go-live: ../shared-infra is not checked out beside this repository" >&2; exit 1; }
  ./with-secrets ./pubdeploy walk --fulfil --server sustenaut --zones ../shared-infra/pubkit.json
fi
