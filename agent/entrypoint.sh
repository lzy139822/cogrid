#!/bin/sh
# Cogrid Agent Docker 入口脚本
# 根据环境变量构造启动参数

ARGS="--coordinator ${COGRID_COORDINATOR_URL:-http://coordinator:8000/api/v1}"
ARGS="$ARGS --name ${COGRID_NODE_NAME:-agent}"
ARGS="$ARGS --intensity ${COGRID_INTENSITY:-balanced}"

# 认证：用户名+密码 或 token
if [ -n "$COGRID_USERNAME" ] && [ -n "$COGRID_PASSWORD" ]; then
  ARGS="$ARGS --username $COGRID_USERNAME --password $COGRID_PASSWORD"
fi
if [ -n "$COGRID_AUTH_TOKEN" ]; then
  ARGS="$ARGS --token $COGRID_AUTH_TOKEN"
fi

echo "Starting agent with: $ARGS"
exec python -m agent.main $ARGS
