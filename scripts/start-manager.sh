#!/usr/bin/sh

manager_name="$1"
manager=$(mongosh wso -f ./scripts/config.js | jq ".managers.[] | select(.name == \"$manager_name\")")
port=$(echo "$manager" | jq '.port' -r)

WSOMGR_MANAGER_NAME="$manager_name" \
  WSOMGR_CONNECTION_STRING="mongodb://localhost/wso" \
  fastapi dev ./manager/main.py --port "$port" --host 0.0.0.0
