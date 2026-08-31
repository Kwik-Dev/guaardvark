# Agent Learning Principles

This is the default public policy for agent knowledge files. Local
installations may keep private operating notes in ignored local files, but
the repository version should stay generic and safe for new users.

## Core Rule

Stored knowledge must describe what the agent should recognize and what it
should do. It must not store screen coordinates as reusable instructions.

Good knowledge is vision-actionable:

- "Find the browser address bar, then type the URL."
- "Find the primary submit button, then click it."
- "If a confirmation dialog is visible, choose the affirmative action."

Bad knowledge bakes in one machine's layout:

- "Click at x=92, y=103."
- "The button is always near y=660."
- "Avoid pixels 690 through 720."

## Why

Windows move, themes change, displays scale differently, and web pages
redesign. A coordinate that works on one machine can fail on another. The
agent should use the current screen to locate targets each time.

## Where Coordinates Are Allowed

Coordinates may appear as short-lived runtime evidence:

- Fresh output from a vision model for the current frame.
- Debug or telemetry records used to evaluate a click attempt.
- Screenshot annotation or UI measurement code.

Coordinates should not appear in durable memories, lessons, recipes, or
prompt-injected knowledge that will be reused across sessions.

## How To Write Knowledge

1. Describe visible targets in plain language.
2. Phrase assumptions as hypotheses, not guarantees.
3. Prefer short target descriptions for detector-facing fields.
4. Put richer context in reasoning-facing notes.
5. Test by changing the layout and verifying the agent can still find the
   target from the screen.

## User Override

Users may keep private local guidance for their own machine. Keep those
files out of Git and out of public release artifacts unless they are
rewritten as generic defaults.
