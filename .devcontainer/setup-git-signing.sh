#!/usr/bin/env bash
# =============================================================================
# Configure Git SSH commit signing inside the dev container.
#
# VS Code automatically forwards the host's SSH agent into the container and
# sets SSH_AUTH_SOCK. This script reads the public key from that forwarded
# agent and points Git's signing config at it, so commits made in the
# container are signed with the same key as on the host.
#
# Prerequisite on the HOST: load your key into the agent before opening the
# container, e.g.:
#   macOS:         ssh-add --apple-use-keychain ~/.ssh/id_ed25519_naviant
#   Linux / WSL:   ssh-add ~/.ssh/id_ed25519_naviant
#   Windows (PS):  Start-Service ssh-agent; ssh-add $env:USERPROFILE\.ssh\id_ed25519_naviant
# =============================================================================
set -euo pipefail

if [[ -z "${SSH_AUTH_SOCK:-}" ]]; then
  echo "ℹ️  No SSH agent forwarded (SSH_AUTH_SOCK unset) — skipping signing setup."
  exit 0
fi

# Read the first public key the forwarded agent holds.
PUBKEY="$(ssh-add -L 2>/dev/null | head -n1 || true)"

if [[ -z "${PUBKEY}" || "${PUBKEY}" == *"agent has no identities"* ]]; then
  echo ""
  echo "⚠️  SSH agent is forwarded but holds no keys, so commit signing is not"
  echo "    configured. On your HOST machine, load your signing key, then reopen"
  echo "    or rebuild the container:"
  echo ""
  echo "      macOS:         ssh-add --apple-use-keychain ~/.ssh/id_ed25519_naviant"
  echo "      Linux / WSL:   ssh-add ~/.ssh/id_ed25519_naviant"
  echo "      Windows (PS):  Start-Service ssh-agent; ssh-add \$env:USERPROFILE\\.ssh\\id_ed25519_naviant"
  echo ""
  # Don't leave Git trying to sign with a key it can't use.
  git config --global commit.gpgsign false
  exit 0
fi

mkdir -p "${HOME}"/.ssh
chmod 700 "${HOME}"/.ssh
echo "${PUBKEY}" > "${HOME}"/.ssh/signing_key.pub
chmod 644 "${HOME}"/.ssh/signing_key.pub

git config --global gpg.format ssh
git config --global user.signingkey "${HOME}"/.ssh/signing_key.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true

# Keep the committer email aligned with the signing key. GitHub only marks a
# commit "Verified" when the committer email is a verified email on the same
# account that owns the signing key. The key's comment (3rd field) is that
# identity, so derive user.email from it to prevent drift (e.g. a stale
# personal email leaving commits Unverified).
KEY_COMMENT="$(echo "${PUBKEY}" | awk '{print $3}')"
CURRENT_EMAIL="$(git config --global user.email || echo "")"
if [[ "${KEY_COMMENT}" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  if [[ "${CURRENT_EMAIL}" != "${KEY_COMMENT}" ]]; then
    git config --global user.email "${KEY_COMMENT}"
    echo "ℹ️  Set git user.email to '${KEY_COMMENT}' (from signing key) to match GitHub verification."
  fi
  SIGNER_EMAIL="${KEY_COMMENT}"
else
  echo "⚠️  Signing key comment ('${KEY_COMMENT:-none}') is not an email; leaving"
  echo "    user.email as '${CURRENT_EMAIL:-unset}'. If commits show as Unverified"
  echo "    on GitHub, set: git config --global user.email <email-on-key-account>"
  SIGNER_EMAIL="${CURRENT_EMAIL}"
fi

# Allow local `git log --show-signature` verification.
if [[ -n "${SIGNER_EMAIL}" ]]; then
  mkdir -p "${HOME}"/.config/git
  echo "${SIGNER_EMAIL} ${PUBKEY}" > "${HOME}"/.config/git/allowed_signers
  git config --global gpg.ssh.allowedSignersFile "${HOME}"/.config/git/allowed_signers
fi

echo "✅ Git SSH commit signing configured (key: ${PUBKEY%% *} …, email: ${SIGNER_EMAIL:-unset})."
