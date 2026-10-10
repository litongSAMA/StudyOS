"""Optional independent core server; requires an explicit DB and environment token."""
import argparse
import hmac
import os
from wsgiref.simple_server import make_server

from .api import AgentAPI
from .service import AgentService


def main():
    parser = argparse.ArgumentParser(description="StudyOS 独立 Agent 核心服务（不替代原应用后端）")
    parser.add_argument("--db", required=True, help="显式指定学习数据库")
    parser.add_argument("--init", action="store_true", help="初始化独立测试库的核心表")
    parser.add_argument("--files", help="允许读取的已上传附件目录")
    parser.add_argument("--port", type=int, default=8801)
    parser.add_argument("--token-env", default="STUDYOS_AGENT_TOKEN")
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token or len(token) < 24:
        parser.error(f"请通过 {args.token_env} 提供至少 24 字符的服务令牌")
    service = AgentService(args.db, bootstrap=args.init, file_root=args.files)

    def authorize(environ):
        value = environ.get("HTTP_AUTHORIZATION", "")
        return "local" if hmac.compare_digest(value.encode(), ("Bearer " + token).encode()) else None

    app = AgentAPI(service, authorize=authorize)
    with make_server("127.0.0.1", args.port, app) as server:
        print(f"StudyOS Agent core: http://127.0.0.1:{args.port}（仅本机，需要授权令牌）")
        server.serve_forever()


if __name__ == "__main__":
    main()
