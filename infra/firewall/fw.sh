#!/usr/bin/env bash
set -euo pipefail

RULES="${FW_RULES:-/etc/shop-fw/rules.conf}"
CHAIN=SHOP-FW
SCOPE=10.10.0.0/16
BRIDGE_SYSCTL=/proc/sys/net/bridge/bridge-nf-call-iptables
WATCH_SECONDS=10
LOG_LIMIT=20/min
IPT=""
RULE_LINES=()

die() {
  echo "fw: $*" >&2
  exit 1
}

usage() {
  die "usage: fw run | apply | restore | remove | show | check | break <rule> | log [count]"
}

detect() {
  local candidate saved
  for candidate in iptables-nft iptables-legacy; do
    saved="$("$candidate-save" -t filter 2>/dev/null || true)"
    if grep -q '^:DOCKER-USER ' <<< "$saved"; then
      IPT="$candidate"
      return 0
    fi
  done
  die "chain DOCKER-USER not found in iptables-nft or iptables-legacy: check that docker runs with its iptables firewall backend"
}

bridge_filtering() {
  if [[ ! -e "$BRIDGE_SYSCTL" ]]; then
    echo "== loading br_netfilter"
    modprobe br_netfilter || true
  fi
  if [[ ! -e "$BRIDGE_SYSCTL" ]]; then
    die "br_netfilter is not loaded and cannot be loaded from the container; on the host run: sudo modprobe br_netfilter && echo br_netfilter | sudo tee /etc/modules-load.d/br_netfilter.conf"
  fi
  if [[ "$(cat "$BRIDGE_SYSCTL")" != 1 ]]; then
    echo "== setting bridge-nf-call-iptables=1"
    echo 1 > "$BRIDGE_SYSCTL"
  fi
}

segment_of() {
  local first="${1%%-*}" last="${1##*-}"
  if [[ "${first%.*}" != "${last%.*}" || "${first%.*}" != 10.10.* ]]; then
    return 1
  fi
  echo "${first%.*}"
}

load_rules() {
  local n=0 name src dst proto ports extra src_segment dst_segment
  RULE_LINES=()
  [[ -r "$RULES" ]] || die "rules file $RULES is not readable"
  while read -r name src dst proto ports extra || [[ -n "${name:-}" ]]; do
    n=$((n + 1))
    [[ -n "${name:-}" ]] || continue
    if [[ -n "${extra:-}" || ! "$name" =~ ^[a-z0-9-]+$ || ! "${src:-}" =~ ^[0-9.]+(-[0-9.]+)?$ \
      || ! "${dst:-}" =~ ^[0-9.]+(-[0-9.]+)?$ || ! "${proto:-}" =~ ^(tcp|udp)$ || ! "${ports:-}" =~ ^[0-9:,]+$ ]]; then
      die "$RULES line $n is invalid, expected: name src-range dst-range tcp|udp ports"
    fi
    src_segment="$(segment_of "$src")" || die "$RULES line $n: $src is not inside one 10.10.S.0/24 segment"
    dst_segment="$(segment_of "$dst")" || die "$RULES line $n: $dst is not inside one 10.10.S.0/24 segment"
    if [[ "$src_segment" != "$dst_segment" ]]; then
      die "$RULES line $n: $name crosses segments $src_segment and $dst_segment, segments are separated by docker networks"
    fi
    RULE_LINES+=("$name $src $dst $proto $ports")
  done < "$RULES"
  (( ${#RULE_LINES[@]} > 0 )) || die "no rules in $RULES"
}

jump_present() {
  "$IPT" -C DOCKER-USER -s "$SCOPE" -d "$SCOPE" -j "$CHAIN" 2>/dev/null
}

apply() {
  local line name src dst proto ports
  load_rules
  bridge_filtering
  detect
  echo "== applying $CHAIN with $IPT: ${#RULE_LINES[@]} rules from $RULES"
  "$IPT" -N "$CHAIN" 2>/dev/null || true
  "$IPT" -F "$CHAIN"
  "$IPT" -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -m comment --comment established -j ACCEPT
  for line in "${RULE_LINES[@]}"; do
    read -r name src dst proto ports <<< "$line"
    "$IPT" -A "$CHAIN" -m iprange --src-range "$src" --dst-range "$dst" -p "$proto" -m multiport --dports "$ports" \
      -m comment --comment "$name" -j ACCEPT || die "cannot add rule $line"
  done
  "$IPT" -A "$CHAIN" -m limit --limit "$LOG_LIMIT" --limit-burst 20 -m comment --comment default-deny \
    -j LOG --log-prefix "SHOP-DROP " --log-level 4
  "$IPT" -A "$CHAIN" -m comment --comment default-deny -j DROP
  if ! jump_present; then
    "$IPT" -I DOCKER-USER 1 -s "$SCOPE" -d "$SCOPE" -j "$CHAIN"
  fi
  echo "== $CHAIN is active for traffic $SCOPE -> $SCOPE"
}

remove() {
  detect
  while jump_present; do
    "$IPT" -D DOCKER-USER -s "$SCOPE" -d "$SCOPE" -j "$CHAIN"
  done
  "$IPT" -F "$CHAIN" 2>/dev/null || true
  "$IPT" -X "$CHAIN" 2>/dev/null || true
  echo "== $CHAIN removed"
}

break_rule() {
  local target="${1:-}" found=0 line name src dst proto ports names=""
  [[ -n "$target" ]] || usage
  load_rules
  detect
  jump_present || die "$CHAIN is not active"
  for line in "${RULE_LINES[@]}"; do
    read -r name src dst proto ports <<< "$line"
    names+=" $name"
    [[ "$name" == "$target" ]] || continue
    found=1
    "$IPT" -I "$CHAIN" 1 -m iprange --src-range "$src" --dst-range "$dst" -p "$proto" -m multiport --dports "$ports" \
      -m comment --comment "break:$name" -j DROP
    "$IPT" -I "$CHAIN" 1 -m iprange --src-range "$src" --dst-range "$dst" -p "$proto" -m multiport --dports "$ports" \
      -m limit --limit "$LOG_LIMIT" --limit-burst 20 -m comment --comment "break:$name" \
      -j LOG --log-prefix "SHOP-BREAK " --log-level 4
  done
  (( found )) || die "rule $target not found, rules:$names"
  echo "== $target is blocked, open connections included; undo with: make fw-restore"
}

show() {
  detect
  echo "== backend $IPT, bridge-nf-call-iptables=$(cat "$BRIDGE_SYSCTL" 2>/dev/null || echo missing)"
  echo "== DOCKER-USER"
  "$IPT" -L DOCKER-USER -n -v --line-numbers
  echo "== $CHAIN"
  "$IPT" -L "$CHAIN" -n -v --line-numbers
}

check() {
  detect
  [[ "$(cat "$BRIDGE_SYSCTL" 2>/dev/null || true)" == 1 ]] || die "bridge-nf-call-iptables is not 1"
  jump_present || die "jump from DOCKER-USER to $CHAIN is missing"
}

log_drops() {
  local count="${1:-20}" kernel_log lines
  [[ "$count" =~ ^[0-9]+$ ]] || usage
  kernel_log="$(dmesg --ctime)" || die "cannot read the kernel log"
  lines="$(grep -E 'SHOP-(DROP|BREAK) ' <<< "$kernel_log" | tail -n "$count" || true)"
  if [[ -z "$lines" ]]; then
    echo "== no blocked packets in the kernel log"
    return 0
  fi
  awk '
    function role(ip,   parts, host) {
      split(ip, parts, ".")
      host = parts[4] + 0
      if (host == 1) return "host"
      if (host == 2) return "gateway"
      if (host == 3) return "tailscale"
      if (host >= 11 && host <= 12) return "front"
      if (host >= 21 && host <= 23) return "api"
      if (host >= 31 && host <= 32) return "worker"
      if (host >= 41 && host <= 42) return "postgres"
      if (host == 51) return "redis"
      if (host >= 61 && host <= 63) return "rabbitmq"
      if (host >= 71 && host <= 73) return "etcd"
      if (host == 81) return "backup"
      if (host == 82) return "db-migrate"
      if (host == 110) return "locust"
      return "?"
    }
    {
      end = index($0, "]")
      stamp = substr($0, 2, end - 2)
      kind = ($0 ~ /SHOP-BREAK/) ? "BREAK" : "DROP"
      net = ""; src = ""; dst = ""; port = ""; proto = ""
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^IN=/) net = substr($i, 4)
        else if ($i ~ /^SRC=/) src = substr($i, 5)
        else if ($i ~ /^DST=/) dst = substr($i, 5)
        else if ($i ~ /^DPT=/) port = substr($i, 5)
        else if ($i ~ /^PROTO=/) proto = substr($i, 7)
      }
      printf "%s  %-5s %-11s %s %s -> %s %s:%s %s\n", stamp, kind, net, role(src), src, role(dst), dst, port, proto
    }
  ' <<< "$lines"
}

run() {
  apply
  trap 'remove; exit 0' TERM INT
  echo "== watching the jump from DOCKER-USER every $WATCH_SECONDS s"
  while true; do
    sleep "$WATCH_SECONDS" &
    wait $! || true
    if ! jump_present; then
      echo "== jump from DOCKER-USER to $CHAIN disappeared, applying again"
      apply
    fi
  done
}

action="${1:-}"
shift || true
case "$action" in
  run) run ;;
  apply | restore) apply ;;
  remove) remove ;;
  show) show ;;
  check) check ;;
  break) break_rule "$@" ;;
  log) log_drops "$@" ;;
  *) usage ;;
esac
