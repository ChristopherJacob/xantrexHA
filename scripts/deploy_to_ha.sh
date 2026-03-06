#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy_to_ha.sh [--restart]

Required environment variables:
  HA_HOST         Home Assistant host or IP
  HA_CONFIG_PATH  Path to HA config dir on remote host (example: /config)

Optional environment variables:
  HA_SSH_USER     SSH user (default: root)
  HA_SSH_PORT     SSH port (default: 22)
  HA_RESTART_CMD  Restart command for HA host (default: ha core restart)
EOF
}

RESTART=0
if [[ "${1:-}" == "--restart" ]]; then
  RESTART=1
elif [[ $# -gt 0 ]]; then
  usage
  exit 1
fi

: "${HA_HOST:?HA_HOST is required}"
: "${HA_CONFIG_PATH:?HA_CONFIG_PATH is required}"

HA_SSH_USER="${HA_SSH_USER:-root}"
HA_SSH_PORT="${HA_SSH_PORT:-22}"
HA_RESTART_CMD="${HA_RESTART_CMD:-ha core restart}"

REMOTE="${HA_SSH_USER}@${HA_HOST}"
REMOTE_COMPONENT_PATH="${HA_CONFIG_PATH%/}/custom_components/xantrex_freedom_x"
LOCAL_COMPONENT_PATH="custom_components/xantrex_freedom_x/"

echo "Creating remote path: ${REMOTE_COMPONENT_PATH}"
ssh -p "${HA_SSH_PORT}" "${REMOTE}" "mkdir -p '${REMOTE_COMPONENT_PATH}'"

echo "Syncing integration files to ${REMOTE}:${REMOTE_COMPONENT_PATH}"
rsync -az --delete \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  -e "ssh -p ${HA_SSH_PORT}" \
  "${LOCAL_COMPONENT_PATH}" \
  "${REMOTE}:${REMOTE_COMPONENT_PATH}/"

if [[ "${RESTART}" -eq 1 ]]; then
  echo "Restarting Home Assistant with: ${HA_RESTART_CMD}"
  ssh -p "${HA_SSH_PORT}" "${REMOTE}" "${HA_RESTART_CMD}"
fi

echo "Deploy complete."
