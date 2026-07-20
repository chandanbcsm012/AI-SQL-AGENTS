"""Minimal CLI to approve/edit/reject SQL sitting in the review queue.

Usage:
    uv run python -m human_review.cli list
    uv run python -m human_review.cli show <review_id>
    uv run python -m human_review.cli approve <review_id> --sql "SELECT ..." --reviewer alice
    uv run python -m human_review.cli reject <review_id> --reason "..." --reviewer alice
"""
import argparse

from human_review import queue


def cmd_list(_args) -> None:
    rows = queue.list_pending()
    if not rows:
        print("No pending reviews.")
        return
    for row in rows:
        print(f"[{row['review_id']}] trace={row['trace_id']} query={row['user_query_masked']!r}")


def cmd_show(args) -> None:
    row = queue.get(args.review_id)
    if not row:
        print(f"No review with id {args.review_id}")
        return
    for key in row.keys():
        if key == "sql_attempts":
            print("sql_attempts:")
            for attempt in queue.get_sql_attempts(row):
                print(f"  #{attempt['attempt']}: valid={attempt['valid']} sql={attempt['sql']!r} error={attempt['error']!r}")
        else:
            print(f"{key}: {row[key]}")


def cmd_approve(args) -> None:
    row = queue.get(args.review_id)
    sql = args.sql or queue.latest_failed_sql(row)
    queue.decide(args.review_id, approved=True, reviewer=args.reviewer, decision_sql=sql)
    print(f"Approved review {args.review_id} with SQL:\n{sql}")


def cmd_reject(args) -> None:
    queue.decide(
        args.review_id,
        approved=False,
        reviewer=args.reviewer,
        decision_reason=args.reason,
    )
    print(f"Rejected review {args.review_id}: {args.reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Human review queue CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p_show = sub.add_parser("show")
    p_show.add_argument("review_id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("review_id", type=int)
    p_approve.add_argument("--sql", default=None, help="Corrected SQL (defaults to last attempt)")
    p_approve.add_argument("--reviewer", required=True)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject")
    p_reject.add_argument("review_id", type=int)
    p_reject.add_argument("--reason", required=True)
    p_reject.add_argument("--reviewer", required=True)
    p_reject.set_defaults(func=cmd_reject)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
