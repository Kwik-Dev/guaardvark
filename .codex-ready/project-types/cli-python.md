# Project Type: Python CLI Package

Guaardvark ships a standalone Python CLI, published to PyPI as `guaardvark`, with its
own lite-server fallback. It talks to the backend over socket.io/HTTP.

- Location: `cli/llx/`, entry point `guaardvark=llx.main:run` (defined in
  `cli/setup.py` `entry_points["console_scripts"]`).
- Own deps in `cli/requirements.txt`; it has its own test suite.

## Commands

```bash
pip install -e ./cli pytest   # install for dev (Python 3.12)
python -m pytest cli/tests -q # run CLI test suite
```

## Known gotchas

- **Python 3.12 only.** The ML stack (numpy<2.0, mediapipe, basicsr/gfpgan,
  realesrgan) has no wheels for 3.13/3.14. Create venvs with `python3.12`.
  `setuptools<81` is pinned because some ML libs still import `pkg_resources`.
- The CLI is independent from the backend package; reuse its own requirements and
  test layout rather than coupling to `backend/`.
