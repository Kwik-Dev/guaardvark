"""Optional: add checks to the inbound guard and hear its verdicts.

register(registry) runs once in the backend and in each Celery worker:
  registry.scanner(name, fn)   fn(changes, context) -> [registry.Finding(...)]; may add findings, never remove
  registry.listener(name, fn)  fn(event) for each verdict and each landed change, on a background thread
  registry.Finding             (rule, pack, severity, path, line, excerpt, why)
  registry.mode()              "off", "observe" or "enforce"
"""


def register(registry):
    """The template adds nothing."""
