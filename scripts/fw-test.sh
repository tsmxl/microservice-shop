#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

NETSHOOT=nicolaka/netshoot:v0.13

CASES=(
  "allow ts-gw        tailscale  10.10.0.2  80    edge   L4"
  "allow gw-front     gateway    10.10.1.11 8080  front  L4"
  "allow front-apilb  front      10.10.1.2  8000  front  L4"
  "allow gw-api       gateway    10.10.2.21 8000  back   L4"
  "allow api-pg       api        10.10.3.41 5432  data   L4"
  "allow worker-pg    worker     10.10.3.41 5432  data   L4"
  "allow migrate-pg   db-migrate 10.10.3.41 5432  data   L4"
  "allow api-redis    api        10.10.3.51 6379  data   L4"
  "allow worker-redis worker     10.10.3.51 6379  data   L4"
  "allow api-mq       api        10.10.4.61 5672  broker L4"
  "allow worker-mq    worker     10.10.4.61 5672  broker L4"
  "deny  -            redis      10.10.3.41 5432  data   L4"
  "deny  -            worker     10.10.3.21 8000  data   L4"
  "deny  -            worker     10.10.4.61 15672 broker L4"
  "deny  -            front      10.10.1.2  80    front  L4"
  "deny  -            tailscale  10.10.0.2  8000  edge   L4"
  "deny  -            front      10.10.2.21 8000  -      L3"
  "deny  -            front      10.10.3.41 5432  -      L3"
  "deny  -            gateway    10.10.3.41 5432  -      L3"
  "deny  -            tailscale  10.10.2.21 8000  -      L3"
)

PROBE='for target in "$@"; do
  if out="$(nc -vz -w 2 "${target%:*}" "${target#*:}" 2>&1)"; then
    state=open
  else
    case "$out" in
      *refused*) state=refused ;;
      *nreachable*|*"No route"*) state=no-route ;;
      *) state=timeout ;;
    esac
  fi
  echo "$target $state"
done'

role() {
  local host="${1##*.}"
  case "$host" in
    2) echo gateway ;;
    3) echo tailscale ;;
    1[12]) echo front ;;
    2[123]) echo api ;;
    3[12]) echo worker ;;
    4[12]) echo postgres ;;
    51) echo redis ;;
    6[123]) echo rabbitmq ;;
    82) echo db-migrate ;;
    *) echo "?" ;;
  esac
}

network_args() {
  local cid
  if [[ "$1" == db-migrate ]]; then
    NET_ARGS=(--network shop_data --ip 10.10.3.82)
    return 0
  fi
  cid="$(docker compose ps -q "$1" | head -n 1)"
  [[ -n "$cid" ]] || return 1
  NET_ARGS=(--network "container:$cid")
}

declare -A RESULT
SOURCES=()
NET_ARGS=()

for line in "${CASES[@]}"; do
  read -r _ _ from _ _ _ _ <<< "$line"
  if [[ " ${SOURCES[*]} " != *" $from "* ]]; then
    SOURCES+=("$from")
  fi
done

docker image inspect "$NETSHOOT" >/dev/null 2>&1 || docker pull -q "$NETSHOOT" >/dev/null

echo "== probes with nc from the network namespace of each source, timeout 2 s"
for from in "${SOURCES[@]}"; do
  targets=()
  for line in "${CASES[@]}"; do
    read -r _ _ source ip port _ _ <<< "$line"
    if [[ "$source" == "$from" ]]; then
      targets+=("$ip:$port")
    fi
  done
  if ! network_args "$from"; then
    for target in "${targets[@]}"; do
      RESULT["$from $target"]=skip
    done
    continue
  fi
  while read -r target state; do
    RESULT["$from $target"]="$state"
  done < <(docker run --rm "${NET_ARGS[@]}" "$NETSHOOT" sh -c "$PROBE" probe "${targets[@]}")
done

pass=0
fail=0
skip=0
printf '%-6s %-13s %-38s %-7s %-6s %-9s %s\n' EXPECT RULE "FROM -> TO:PORT" NET LAYER ACTUAL RESULT
for line in "${CASES[@]}"; do
  read -r expect rule from ip port net layer <<< "$line"
  key="$from $ip:$port"
  actual="${RESULT[$key]:-skip}"
  if [[ "$actual" == skip ]]; then
    verdict="skip, $from is not running"
    skip=$((skip + 1))
  elif [[ "$expect" == allow && "$actual" == open ]] \
    || [[ "$expect" == deny && ( "$actual" == timeout || "$actual" == no-route ) ]]; then
    verdict=ok
    pass=$((pass + 1))
  else
    verdict=FAIL
    fail=$((fail + 1))
  fi
  printf '%-6s %-13s %-38s %-7s %-6s %-9s %s\n' "$expect" "$rule" "$from -> $(role "$ip") $ip:$port" "$net" "$layer" "$actual" "$verdict"
done

echo "== ok $pass, fail $fail, skip $skip"
(( fail == 0 ))
