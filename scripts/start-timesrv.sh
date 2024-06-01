#!/usr/bin/sh

config=$(mongosh wso -f ./scripts/config.js)
port=$(echo "$config" | jq  ".services.[] | select(.name == \"timesrv\") | .port")

manager_name="$1"
vms=$(mongosh wso -f ./scripts/plan.js | jq '.vms')

echo "Choose VM:"
i=1
for token in $(echo "$vms" | jq '.[].token' -r); do
  echo "$i. $token"
  i=$((i+1))
done
read -r i
i=$((i-1))

vm=$(echo "$vms" | jq ".[$i]" -r)
token=$(echo "$vm" | jq '.token' -r)

manager_name=$(echo "$vm" | jq '.manager' -r)
manager=$(echo "$config" | jq ".managers.[] | select(.name == \"$manager_name\")")
manager_address="$(echo "$manager" | jq '.address' -r):$(echo "$manager" | jq '.port' -r)"

WSOTIMESRV_MANAGER_ADDRESS="$manager_address" \
  WSOTIMESRV_TOKEN="$token" \
  fastapi dev ./timesrv/main.py --port "$port"
