#!/usr/bin/env bash
# Idempotently creates the Always Free network + A1 ARM VM in Oracle Cloud and prints the public IP.
# Runs in GitHub Actions. Auth comes from OCI_CLI_USER / OCI_CLI_TENANCY / OCI_CLI_FINGERPRINT /
# OCI_CLI_KEY_CONTENT / OCI_CLI_REGION. Needs: oci-cli, jq, and SSH_PUBKEY_FILE.
# Free-tier guard: only VM.Standard.A1.Flex, <= 2 OCPU / 12 GB / 50 GB boot (limit is 4 / 24 / 200 per tenancy).
set -euo pipefail
export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True
C="${OCI_CLI_TENANCY:?}"      # root compartment
NAME=pulse-api
SHAPE=VM.Standard.A1.Flex OCPUS=2 MEM_GB=12 BOOT_GB=50
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${SSH_PUBKEY_FILE:?}"

o() { oci "$@" --output json; }
log() { echo "[oci] $*" >&2; }

existing() {
  o compute instance list -c "$C" --display-name "$NAME" --all \
    | jq -r '[.data[] | select(."lifecycle-state" | IN("RUNNING","PROVISIONING","STARTING"))][0].id // empty'
}

INST="$(existing)"
if [ -z "$INST" ]; then
  # ---- network (VCN, internet gateway, route, security list, public subnet) ----
  VCN="$(o network vcn list -c "$C" --display-name pulse-vcn --all | jq -r '.data[0].id // empty')"
  if [ -z "$VCN" ]; then
    log "creating VCN"
    VCN="$(o network vcn create -c "$C" --display-name pulse-vcn --cidr-block 10.0.0.0/16 --dns-label pulse \
      --wait-for-state AVAILABLE | jq -r .data.id)"
  fi
  IGW="$(o network internet-gateway list -c "$C" --vcn-id "$VCN" --all | jq -r '.data[0].id // empty')"
  if [ -z "$IGW" ]; then
    IGW="$(o network internet-gateway create -c "$C" --vcn-id "$VCN" --is-enabled true --display-name pulse-igw \
      --wait-for-state AVAILABLE | jq -r .data.id)"
  fi
  VCN_JSON="$(o network vcn get --vcn-id "$VCN")"
  RT="$(jq -r '.data."default-route-table-id"' <<<"$VCN_JSON")"
  SL="$(jq -r '.data."default-security-list-id"' <<<"$VCN_JSON")"
  o network route-table update --rt-id "$RT" --force \
    --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IGW\"}]" >/dev/null
  tcp() { printf '{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":%s,"max":%s}}}' "$1" "$1"; }
  o network security-list update --security-list-id "$SL" --force \
    --ingress-security-rules "[$(tcp 22),$(tcp 80),$(tcp 443)]" \
    --egress-security-rules '[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]' >/dev/null
  SUBNET="$(o network subnet list -c "$C" --vcn-id "$VCN" --all | jq -r '.data[0].id // empty')"
  if [ -z "$SUBNET" ]; then
    SUBNET="$(o network subnet create -c "$C" --vcn-id "$VCN" --cidr-block 10.0.0.0/24 --display-name pulse-subnet \
      --dns-label pulse --wait-for-state AVAILABLE | jq -r .data.id)"
  fi

  # ---- image: newest Ubuntu 24.04 built for this ARM shape ----
  IMG="$(o compute image list -c "$C" --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
    --shape "$SHAPE" --sort-by TIMECREATED --sort-order DESC --limit 1 | jq -r '.data[0].id // empty')"
  [ -n "$IMG" ] || { log "no Ubuntu 24.04 aarch64 image found in this region"; exit 1; }

  # ---- launch; ARM capacity is scarce, so cycle through ADs and retry for ~25 min ----
  mapfile -t ADS < <(o iam availability-domain list -c "$C" | jq -r '.data[].name')
  for round in $(seq 1 25); do
    for AD in "${ADS[@]}"; do
      log "launching $SHAPE ($OCPUS OCPU / $MEM_GB GB) in $AD (round $round)"
      if OUT="$(oci compute instance launch --output json --availability-domain "$AD" -c "$C" --shape "$SHAPE" \
          --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEM_GB}" --image-id "$IMG" --subnet-id "$SUBNET" \
          --assign-public-ip true --display-name "$NAME" --boot-volume-size-in-gbs "$BOOT_GB" \
          --ssh-authorized-keys-file "$SSH_PUBKEY_FILE" --user-data-file "$HERE/cloud-init.yaml" \
          --wait-for-state RUNNING 2>&1)"; then
        INST="$(jq -r .data.id <<<"$OUT")"; break 2
      fi
      grep -qi "out of host capacity\|OutOfHostCapacity\|LimitExceeded.*capacity" <<<"$OUT" || { echo "$OUT" >&2; exit 1; }
    done
    log "no ARM capacity yet; sleeping 60s"; sleep 60
  done
  [ -n "$INST" ] || { log "still out of host capacity after retries; re-run the workflow later"; exit 1; }
fi

IP="$(o compute instance list-vnics --instance-id "$INST" | jq -r '.data[0]."public-ip"')"
[ -n "$IP" ] && [ "$IP" != null ] || { log "instance has no public IP yet"; exit 1; }
log "instance $INST at $IP"
echo "$IP"
