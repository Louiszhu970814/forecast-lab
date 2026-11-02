"""Local coordinator interface. Telegram only reads this board / chooses published options."""
import argparse
import json
from pathlib import Path
from flab.team_chat import Board
from flab.dev_tasks import TaskError


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    pub = sub.add_parser('publish'); pub.add_argument('--file', type=Path, required=True); pub.add_argument('--source', required=True)
    ask = sub.add_parser('ask'); ask.add_argument('--file', type=Path, required=True)
    close = sub.add_parser('close'); close.add_argument('decision_id'); close.add_argument('summary')
    args = p.parse_args(argv); board = Board(args.project)
    try:
        if args.command == 'publish':
            board.publish(args.file.read_text(), source=args.source)
        elif args.command == 'ask':
            board.ask(**json.loads(args.file.read_text()))
        elif args.command == 'close':
            board.close(args.decision_id, args.summary)
        print(board.snapshot())
    except (TaskError, ValueError, OSError):
        print('操作未完成：請檢查輸入與帳冊，未自動重試。')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
