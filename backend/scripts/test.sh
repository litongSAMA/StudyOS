#!/usr/bin/env bash
# StudyOS 后端回归测试：隔离临时数据库，绝不触碰 data/studyos.db
# 用法：./scripts/test.sh [pytest 参数...]
#   例：./scripts/test.sh tests/test_agent_extract.py -v
set -e
cd "$(dirname "$0")/.."   # backend/

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi
./.venv/bin/pip install -q -r requirements-dev.txt
exec ./.venv/bin/python -m pytest "$@"
