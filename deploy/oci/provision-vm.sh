#!/usr/bin/env bash
# Idempotently creates the Always Free network + VM in Oracle Cloud and prints the public IP.
# Runs in GitHub Actions. Auth comes from OCI_CLI_USER / OCI_CLI_TENANCY / OCI_CLI_FINGERPRINT /
# OCI_CLI_KEY_CONTENT / OCI_CLI_REGION. Needs: oci-cli, jq, and SSH_PUBKEY_FILE.
# Free-tier guard: only VM.Standard.A1.Flex (<= 2 OCPU / 12 GB / 50 GB boot; limit is 4 / 24 / 200 per tenancy)
# or, as a fallback when ARM capacity is exhausted, VM.Standard.E2.1.Micro (Always Free, 1 GB, x86).
set -euo pipefail
export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True
C="${OCI_CLI_TENANCY:?}"      # root compartment
NAME=pulse-api
SHAPE=VM.Standard.A1.Flex OCPUS=2 MEM_GB=12 BOOT_GB=50
MICRO=VM.Standard.E2.1.Micro
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${SSH_PUBKEY_FILE:?}"

o() { oci "$@" --output json; }
log() { echo "[oci] $*" >&2; }

existing() {
  o compute instance list -c "$C" --display-name "$NAME" --all \
    | jq -r '[.data[] | select(."lifecycle-state" | IN("RUNNING","PROVISIONING","STARTING"))][0].id // empty'
}

image_for() {
  o compute image list -c "$C" --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
    --shape "$1" --sort-by TIMECREATED --sort-order DESC --limit 1 | jq -r '.data[0].id // empty'
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

  # ---- images: newest Ubuntu 24.04 for each free shape ----
  IMG="$(image_for "$SHAPE")"
  [ -n "$IMG" ] || { log "no Ubuntu 24.04 aarch64 image found in this region"; exit 1; }
  IMG_MICRO="$(image_for "$MICRO")"
  [ -n "$IMG_MICRO" ] || log "no Ubuntu 24.04 image for $MICRO; ARM only"

  # try_launch SHAPE IMAGE [shape-config-json]; sets INST on success.
  # Returns 0 launched/picked up, 1 no capacity or throttled (retry), exits on any other error.
  try_launch() {
    local shape="$1" image="$2" cfg="${3:-}" AD OUT
    local -a extra=()
    [ -z "$cfg" ] || extra=(--shape-config "$cfg")
    for AD in "${ADS[@]}"; do
      log "launching $shape in $AD (round $round)"
      if OUT="$(oci compute instance launch --output json --availability-domain "$AD" -c "$C" --shape "$shape" \
          "${extra[@]}" --image-id "$image" --subnet-id "$SUBNET" \
          --assign-public-ip true --display-name "$NAME" --boot-volume-size-in-gbs "$BOOT_GB" \
          --ssh-authorized-keys-file "$SSH_PUBKEY_FILE" --user-data-file "$HERE/cloud-init.yaml" \
          --wait-for-state RUNNING --max-retries 0 2>&1)"; then
        # The CLI prints progress text before the JSON; keep only the JSON, else look the VM up by name.
        INST="$(sed -n '/^{/,$p' <<<"$OUT" | jq -r .data.id 2>/dev/null || true)"
        [ -n "$INST" ] || INST="$(existing)"
        return 0
      fi
      if grep -qi "TooManyRequests" <<<"$OUT"; then
        # A throttled launch may still have created the instance; pick it up if so.
        INST="$(existing)"; [ -z "$INST" ] || return 0
        log "throttled (429); sleeping 90s"; sleep 90; return 1
      fi
      grep -qi "out of host capacity\|OutOfHostCapacity\|LimitExceeded.*capacity" <<<"$OUT" || { echo "$OUT" >&2; exit 1; }
    done
    return 1
  }

  # ---- launch; capacity is scarce, so each round tries ARM then the AMD micro, for ~25 min ----
  mapfile -t ADS < <(o iam availability-domain list -c "$C" | jq -r '.data[].name')
  for round in $(seq 1 25); do
    try_launch "$SHAPE" "$IMG" "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEM_GB}" && break
    if [ -n "$IMG_MICRO" ]; then
      try_launch "$MICRO" "$IMG_MICRO" && { log "using the AMD micro fallback (1 GB)"; break; }
    fi
    log "no capacity yet; sleeping 60s"; sleep 60
  done
  [ -n "$INST" ] || { log "still out of host capacity after retries; re-run the workflow later"; exit 1; }
fi

# ---- off-VM backups: private Object Storage bucket (Always Free: 20 GB) writable only by this VM ----
BUCKET=pulse-backups
NS="$(o os ns get | jq -r .data)"
o os bucket get --namespace-name "$NS" --bucket-name "$BUCKET" >/dev/null 2>&1 || {
  log "creating bucket $BUCKET"
  o os bucket create -c "$C" --namespace-name "$NS" --name "$BUCKET" --storage-tier Standard \
    --public-access-type NoPublicAccess >/dev/null
}
RULE="instance.id = '$INST'"
DG="$(o iam dynamic-group list -c "$C" --name pulse-vm --all | jq -r '.data[0].id // empty')"
if [ -z "$DG" ]; then
  o iam dynamic-group create -c "$C" --name pulse-vm --description "Pulse VM" --matching-rule "$RULE" >/dev/null
else
  o iam dynamic-group update --dynamic-group-id "$DG" --matching-rule "$RULE" --force >/dev/null
fi
# The policy's statements never change, so create it once and leave it alone afterwards
# (an update needs --version-date alongside --statements, which is not worth the trouble).
STMT="[\"Allow dynamic-group pulse-vm to manage objects in tenancy where target.bucket.name='$BUCKET'\",\"Allow dynamic-group pulse-vm to read buckets in tenancy\"]"
POL="$(o iam policy list -c "$C" --name pulse-vm-backups --all | jq -r '.data[0].id // empty')"
if [ -z "$POL" ]; then
  o iam policy create -c "$C" --name pulse-vm-backups --description "Pulse VM backups" --statements "$STMT" >/dev/null
fi

IP="$(o compute instance list-vnics --instance-id "$INST" | jq -r '.data[0]."public-ip"')"
[ -n "$IP" ] && [ "$IP" != null ] || { log "instance has no public IP yet"; exit 1; }
log "instance $INST at $IP"
echo "$IP"
