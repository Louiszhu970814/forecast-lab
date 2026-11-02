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
    handoff = sub.add_parser('handoff'); handoff.add_argument('decision_id'); handoff.add_argument('choice')
    handoff.add_argument('--file', type=Path, required=True)
    claim = sub.add_parser('claim'); claim.add_argument('decision_id'); claim.add_argument('--coordinator', required=True)
    claim.add_argument('--revision', type=int, required=True)
    milestone = sub.add_parser('milestone'); milestone.add_argument('milestone_id'); milestone.add_argument('summary')
    milestone.add_argument('--source', required=True)
    notice = sub.add_parser('notify'); notice.add_argument('--file', type=Path, required=True); notice.add_argument('--source', required=True)
    args = p.parse_args(argv); board = Board(args.project)
    try:
        if args.command == 'publish':
            board.publish(args.file.read_text(), source=args.source)
        elif args.command == 'ask':
            board.ask(**json.loads(args.file.read_text()))
        elif args.command == 'close':
            board.close(args.decision_id, args.summary)
        elif args.command == 'handoff':
            board.handoff(args.decision_id, args.choice, **json.loads(args.file.read_text()))
        elif args.command == 'claim':
            board.claim(args.decision_id, coordinator=args.coordinator, revision=args.revision)
        elif args.command == 'milestone':
            board.complete_milestone(args.milestone_id, args.summary, source=args.source)
        elif args.command == 'notify':
            from telegram_bot import settings
            token, owner = settings(args.project)
            board.notify(f'{token.split(":", 1)[0]}:{owner}', args.file.read_text(), source=args.source)
        print(board.snapshot())
    except (TaskError, ValueError, OSError):
        print('操作未完成：請檢查輸入與帳冊，未自動重試。')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
