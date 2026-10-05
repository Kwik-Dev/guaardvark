"""The three render gates: `--yes`-gated commands that start a render (CLI_PLAN D6).

**This is the only fork module allowed to name a decision route**, and it is deliberately
its own file. The D2 static scan in `test_fork_readonly_contract.py` matches `/approve`,
`/reject`, `/decide`, `/apply`, `/release-held` and `/dispatch` in every fork module outside
a docstring; keeping the three literals here means one file-wide entry in `_ALLOWED` instead
of a rule any future module could quietly lean on.

Why these three, and nothing else:

* **Neither pipeline has a render route.** The render *is* the consequence of the approval.
  `production_service.STAGE_TO_AGENT` marks `casting` and `awaiting_approval` as user-gated
  (`None`), and everything after them self-dispatches — the editor even resumes after a
  backend restart. So a person who wants to render from the terminal must send one of these
  POSTs; there is nothing else to call.
* **They decide what to do with output the operator already owns**, not whether something
  leaves the machine. Picking which storyboard frames become shots, and whether to spend the
  GPU on clips, is the `cast approve` class D2 already allows — not the inbound-guard /
  held-code / publish class it forbids. Those stay Studio-only.
* **`--yes` is required and a refusal sends nothing**, in the same shape as every other
  fork gate (`content page-delete`, `cast train`, `upscale cancel`): `print_error` with
  `CONFIRMATION_REQUIRED` and exit 1. The generic `api request` guard (D5) already calls
  these routes decision-class on the same terms, so the named command and the escape hatch
  gate identically.

The upstream groups are extended the same way the read-only halves are — no `COMMAND_NAME`,
no `app`, commands registered on the upstream app objects at import.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands.film_crew import film_crew_app
from llx.commands.music_video import music_video_app

from ._common import fail, json_mode, resolve_server, success

# Route templates, named rather than inlined so `_ALLOWED` in the contract test can match
# each one exactly. `%d` rather than an f-string keeps each route a plain string literal,
# which is what that scan reads.
CONFIRM_CASTING = "/api/production/%d/casting/confirm"
APPROVE_STORYBOARD = "/api/production/%d/storyboard/approve"
APPROVE_MUSIC_VIDEO = "/api/music-video/%d/approve"


def _gate(server, path: str, *, yes: bool, what: str, transition: str):
    """POST a gate, or refuse and say what to add.

    Shared so the three gates cannot drift: one refusal wording, one exit code, one
    `--json` shape. `output.print_error` already respects an active `--json`, so the
    refusal does not have to know about output modes.
    """
    if not yes:
        output.print_error(
            f"Refusing to {what} without --yes. It moves the production to {transition}.",
            code="CONFIRMATION_REQUIRED",
        )
        raise typer.Exit(1)
    try:
        return get_client(resolve_server(server)).post(path)
    except LlxError as exc:
        fail(exc)


@film_crew_app.command("confirm-casting")
def fc_confirm_casting(
    prod_id: int = typer.Argument(..., help="Production id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Send it (required)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Confirm casting and start the cinematographer. Requires --yes.

    The first render gate: `casting` → `cinematography`. Every identity-locked subject needs
    a trained LoRA with its file still on disk, or the backend answers 400 naming the ones
    missing or stale — `film-crew subjects <id>` shows that state.
    """
    as_json = json_mode(json_out)
    data = _gate(
        server, CONFIRM_CASTING % prod_id,
        yes=yes, what="confirm casting and start storyboards",
        transition="cinematography",
    )
    if as_json:
        success(data)
        return
    output.print_success(
        f"Casting confirmed for production {prod_id} (stage: {data.get('current_stage', '?')})"
    )
    output.print_warning(
        "Storyboards follow, then it stops again at awaiting_approval until "
        "`film-crew approve-storyboard`."
    )


@film_crew_app.command("approve-storyboard")
def fc_approve_storyboard(
    prod_id: int = typer.Argument(..., help="Production id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Send it (required)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Approve the storyboard and START THE RENDER. Requires --yes.

    The second gate: `awaiting_approval` → `rendering`. This spends GPU — every shot is
    rendered to a clip. Review the frames first with `film-crew shots` and
    `film-crew shot <id> <shot> --image`.
    """
    as_json = json_mode(json_out)
    data = _gate(
        server, APPROVE_STORYBOARD % prod_id,
        yes=yes, what="approve the storyboard and start the render",
        transition="rendering",
    )
    if as_json:
        success(data)
        return
    output.print_success(
        f"Storyboard approved for production {prod_id} — "
        f"{data.get('shots_approved', 0)} shots queued (stage: {data.get('current_stage', '?')})"
    )
    output.print_warning("Rendering. Track it with `film-crew shots` or `jobs watch`.")


@music_video_app.command("approve")
def mv_approve(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Send it (required)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Approve the cut plan and release per-clip generation. Requires --yes.

    The cost gate the backend names as such: `awaiting_approval` → `generating`, then every
    clip renders. Review `music-video cuts <id> --prompts` before you send it.
    """
    as_json = json_mode(json_out)
    data = _gate(
        server, APPROVE_MUSIC_VIDEO % mv_id,
        yes=yes, what="approve the cut plan and release per-clip generation",
        transition="generating",
    )
    if as_json:
        success(data)
        return
    output.print_success(
        f"Cut plan approved for music video {mv_id} (stage: {data.get('current_stage', '?')})"
    )
    output.print_warning("Clips are generating. Track them with `music-video clips <id>`.")
