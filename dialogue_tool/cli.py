import argparse
import json
from pathlib import Path

from .service import Service
from .settings import settings


def main():
    parser = argparse.ArgumentParser(description='对话视频工具 Mac 验证版（即梦暂不接入）')
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('desktop', help='启动桌面窗口')
    subs.add_parser('doctor', help='仅检查环境，不提交生成任务')
    create = subs.add_parser('import-video', help='建立项目，原视频保持原位')
    create.add_argument('video', type=Path)
    analyze = subs.add_parser('analyze', help='逐帧扫描并通过 Codex 识别候选组，会使用 Codex 额度')
    analyze.add_argument('project')
    analyze.add_argument('--start', type=float, default=0)
    analyze.add_argument('--end', type=float)
    analyze.add_argument('--scan-only', action='store_true')
    args = parser.parse_args()
    if args.command == 'desktop':
        from .desktop import launch
        launch()
        return
    service = Service(settings())
    if args.command == 'doctor':
        from .desktop import DesktopAPI
        result = DesktopAPI(service).check_environment()
    elif args.command == 'import-video':
        result = service.projects.create(args.video)
    else:
        result = service.analyze(args.project, args.start, args.end, scan_only=args.scan_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
