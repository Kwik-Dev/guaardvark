#!/usr/bin/env python3
"""Run the Phase-5 lesson reconciler from the command line.

Scans every active ``belief_update`` AgentMemory, groups them by source-file/line/element,
and stages a ``PendingFix`` for each cluster that has crossed the agreement
threshold (default 3 sessions).

Usage::

    python scripts/run_lesson_reconciler.py
    python scripts/run_lesson_reconciler.py --threshold 5
    python scripts/run_lesson_reconciler.py --dry-run

``--dry-run`` prints the plan a real run would stage from (same filters, same
candidates) and why each other group at the threshold is left alone.

The same scan also runs on the Celery beat every six hours
(``memory.reconcile_belief_updates``); run it here to stage proposals now.
Either way it only stages them for review and edits no file.
"""

import argparse
import logging
import os
import sys

# Make backend.* importable when invoked from scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--threshold", type=int, default=3,
        help="Minimum number of sessions agreeing before a fix is staged",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be staged, but don't write to the database",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable info-level logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from backend.app import create_app
    app = create_app()

    with app.app_context():
        from backend.services.lesson_reconciler import plan_belief_updates, stage_belief_updates

        if args.dry_run:
            # The same plan a real run stages from, printed instead of written.
            plan = plan_belief_updates(threshold=args.threshold)
            print(
                f"Found {plan.memories} active belief_update memories across "
                f"{plan.groups} group(s)."
            )
            print(f"Would stage {len(plan.candidates)} pending fix(es) at threshold {args.threshold}:")
            _print_candidates(plan.candidates)
            if plan.skipped:
                print(f"{len(plan.skipped)} group(s) at the threshold left alone:")
                for s in sorted(plan.skipped, key=_order):
                    print(f"  - {s.sessions}x: {s.element!r} @ {_where(s)} ({s.reason})")
            return 0

        staged = stage_belief_updates(threshold=args.threshold)
        print(f"Lesson reconciler staged {len(staged)} pending fix(es):")
        _print_candidates(staged)
        return 0


def _order(group):
    return (-group.sessions, group.source_file, group.source_line or 0, group.element)


def _where(group) -> str:
    if group.source_line is None:
        return group.source_file
    return f"{group.source_file}:{group.source_line}"


def _print_candidates(candidates) -> None:
    for c in sorted(candidates, key=_order):
        print(f"  - {c.sessions}x: {c.element!r} @ {_where(c)}")


if __name__ == "__main__":
    sys.exit(main())
