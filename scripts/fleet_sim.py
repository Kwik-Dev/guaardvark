#!/usr/bin/env python3
"""Simulate a fleet of signed Guaardvark machines against a real master.

Each simulated machine has its own data directory, Ed25519 key and node id,
and speaks the protocol in backend/utils/node_auth.py: it enrols with the
fleet key, waits until the operator approves it, collects its certificate,
heartbeats, calls protected node routes and, when given their paths, posts
evidence and decide requests.

Run it on the master. Each simulated machine's requests carry X-Forwarded-For
with its own address from 198.18.0.0/15 (the range set aside for benchmarks);
the master trusts that header only from its own loopback, so it sees each one
as another host on the network, exactly like a real machine. From any other
host the header is ignored and the requests are remote anyway.

    python3 scripts/fleet_sim.py --nodes 1 --approve     # every S1 security check
    python3 scripts/fleet_sim.py --nodes 50 --approve    # a small fleet, end to end
    python3 scripts/fleet_sim.py --nodes 1000 --approve --concurrency 64

The master must be in master mode with the Interconnector enabled. The fleet
key comes from --fleet-key or GX_FLEET_KEY (none is needed when the master does
not ask for one). Approving and removing machines are operator calls: on the
master machine they need nothing more; from elsewhere, or once the install has
an API key, pass it with --api-key or GUAARDVARK_API_KEY.

While a run lasts the simulated machines are real rows in the master's
database, listed in Settings → Interconnector. They declare no services, so
routing never sends work to them, and they are removed at the end unless
--keep is given. Their keys live under --data-dir (default
~/.guaardvark/fleet_sim), readable only by their owner; while enrolled, a key
there is a fleet member's key.

The security checks never touch real data: the directive probe is a name the
master does not act on, and the operator routes are asked from another
address, where they must refuse.

Exit status 0 when every check and call answered as expected, 1 otherwise.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
API = "/api/interconnector"
REGISTER = f"{API}/nodes/register"
TIMEOUT_S = 20


def _load_node_auth():
    # Loaded by path: importing the backend package would start the whole app.
    spec = importlib.util.spec_from_file_location(
        "fleet_sim_node_auth", REPO / "backend" / "utils" / "node_auth.py")
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclass looks itself up there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


node_auth = _load_node_auth()


# ---- simulated machines --------------------------------------------------------

class Machine:
    """One simulated machine: its key, node id, address and certificate."""

    def __init__(self, data_dir: Path, name: str, index: int):
        self.dir = data_dir / name
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        id_file = self.dir / "node_id"
        if not id_file.exists():
            id_file.write_text(str(uuid.uuid4()))
        self.node_id = id_file.read_text().strip()
        self.name = name
        self.key = node_auth.load_or_create_private_key(self.dir)
        self.public_key = node_auth.public_key_text(self.key)
        self.address = f"198.18.{(index // 250) % 256}.{index % 250 + 1}"
        self.cert = None

    def profile(self) -> dict:
        return {"node_id": self.node_id, "hostname": self.name, "arch": "x86_64",
                "services": {}, "fleet_sim": True}


class Master:
    """Calls to the master, as a simulated machine or as the operator."""

    def __init__(self, url: str, fleet_key: str | None, api_key: str | None):
        self.url = url.rstrip("/")
        self.fleet_key = fleet_key
        self.api_key = api_key
        self.master_public_key = None
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = requests.Session()
        return session

    def send(self, method, path, *, machine=None, sign=True, cert=None, enrolment=False,
             body=None, query=None, headers=None, prepared_headers=None):
        """One request. As a machine: from its address, signed with its key and
        certificate (or no certificate, when enrolling). Returns (response, ms)."""
        url = f"{self.url}{path}"
        sent = {"Content-Type": "application/json"} if body is not None else {}
        if machine is not None:
            sent["X-Forwarded-For"] = machine.address
        if enrolment and self.fleet_key:
            sent["X-API-Key"] = self.fleet_key
        sent.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        auth = None
        if machine is not None and sign and prepared_headers is None:
            auth = node_auth.NodeSignature(machine.key, machine.node_id,
                                           None if enrolment else (cert or machine.cert))
        if prepared_headers is not None:
            sent.update(prepared_headers)
        started = time.perf_counter()
        response = self._session().request(method, url, data=data, params=query, headers=sent,
                                           auth=auth, timeout=TIMEOUT_S)
        return response, (time.perf_counter() - started) * 1000

    def operator(self, method, path, body=None):
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        return self.send(method, path, body=body, headers=headers)


# ---- results -------------------------------------------------------------------

class Results:
    def __init__(self):
        self._lock = threading.Lock()
        self.timings = defaultdict(list)
        self.statuses = defaultdict(Counter)
        self.failures = []
        self.checks = []

    def call(self, phase, response, ms, expected):
        with self._lock:
            self.timings[phase].append(ms)
            self.statuses[phase][response.status_code] += 1
            if response.status_code not in expected:
                self.failures.append(
                    f"{phase}: HTTP {response.status_code}, expected {sorted(expected)}: "
                    f"{_error_text(response)}")
                return False
            return True

    def error(self, phase, message):
        with self._lock:
            self.statuses[phase]["error"] += 1
            self.failures.append(f"{phase}: {message}")

    def check(self, task, what, response, expected):
        ok = response.status_code in expected
        with self._lock:
            self.checks.append((task, what, response.status_code, sorted(expected), ok,
                                "" if ok else _error_text(response)))
        return ok


def _error_text(response) -> str:
    try:
        error = response.json().get("error")
    except ValueError:
        return response.text[:160]
    if isinstance(error, dict):
        return str(error.get("message") or "")[:160]
    return str(error or "")[:160]


# ---- one machine's life ----------------------------------------------------------

def enrol(master: Master, machine: Machine, results: Results, approve: bool, phase="enrol"):
    """Enrol; approve as the operator if asked; collect the certificate.
    Returns True when the machine holds a certificate."""
    body = {
        "node_id": machine.node_id,
        "node_name": machine.name,
        "node_mode": "client",
        "hardware_profile": machine.profile(),
        "client_port": 9,
        "sync_entities": [],
        "public_key": machine.public_key,
    }
    response, ms = master.send("POST", REGISTER, machine=machine, enrolment=True, body=body)
    if not results.call(phase, response, ms, {200, 202}):
        return False
    data = response.json().get("data") or {}
    if data.get("node_id") and data["node_id"] != machine.node_id:
        machine.node_id = data["node_id"]
        (machine.dir / "node_id").write_text(machine.node_id)
    if response.status_code == 202:
        if not approve:
            return False
        response, ms = master.operator("POST", f"{API}/enrolments/{machine.node_id}/approve")
        if not results.call("approve", response, ms, {200}):
            return False
        return enrol(master, machine, results, approve=False, phase="enrol-approved")
    return accept_certificate(master, machine, results, data, phase)


def accept_certificate(master, machine, results, data, phase) -> bool:
    key = data.get("master_public_key")
    if master.master_public_key is None:
        master.master_public_key = key
    if key != master.master_public_key:
        results.error(phase, f"{machine.name}: the master answered with another key")
        return False
    try:
        cert = node_auth.read_certificate(data.get("certificate") or "", key)
    except node_auth.NodeAuthError as exc:
        results.error(phase, f"{machine.name}: certificate refused: {exc.reason}")
        return False
    if cert.pub != machine.public_key or cert.node != machine.node_id:
        results.error(phase, f"{machine.name}: certificate names another machine")
        return False
    machine.cert = cert.token
    return True


def heartbeat(master, machine, results, phase="heartbeat", expected=frozenset({200})):
    body = {"hardware_profile": machine.profile(), "sync_entities": []}
    response, ms = master.send("POST", f"{API}/nodes/{machine.node_id}/heartbeat",
                               machine=machine, body=body)
    ok = results.call(phase, response, ms, expected)
    if ok and response.status_code == 200:
        data = response.json().get("data") or {}
        if data.get("certificate"):
            accept_certificate(master, machine, results, data, "renewal")
    return ok


def run_machine(master, machine, results, args) -> bool:
    if not enrol(master, machine, results, args.approve):
        return False
    for _ in range(args.beats):
        heartbeat(master, machine, results)
    response, ms = master.send("GET", f"{API}/sync/pull", machine=machine, query={"entities": "rules"})
    results.call("sync-pull", response, ms, {200})
    response, ms = master.send("GET", f"{API}/updates/registry", machine=machine)
    results.call("updates-registry", response, ms, {200})
    for phase, path in (("evidence", args.evidence_path), ("decide", args.decide_path)):
        if path:
            body = {"node_id": machine.node_id, "fleet_sim": True, "sent_at": int(time.time())}
            response, ms = master.send("POST", path, machine=machine, body=body)
            results.call(phase, response, ms, {200, 201, 202})
    return True


def remove(master, machine, results):
    response, ms = master.operator("DELETE", f"{API}/enrolments/{machine.node_id}")
    results.call("cleanup", response, ms, {200, 404})


# ---- the S1 security checks ------------------------------------------------------

def security_checks(master: Master, a: Machine, data_dir: Path, results: Results, args):
    """Each check from the S1 tasks, with the answer the master must give."""
    hb_path = f"{API}/nodes/{a.node_id}/heartbeat"
    body = {"hardware_profile": a.profile(), "sync_entities": []}
    raw = json.dumps(body).encode()
    extra = []

    # T441: who may heartbeat for a node.
    r, _ = master.send("POST", hb_path, machine=a, sign=False, body=body)
    results.check("T441", "unsigned heartbeat", r, {401})
    r, _ = master.send("POST", hb_path, machine=a, sign=False, body=body,
                       headers={"X-API-Key": master.fleet_key or "fleet-key"})
    results.check("T441", "fleet key alone, no signature", r, {401})
    r, _ = master.send("POST", hb_path, machine=a, body=body)
    results.check("T441", "heartbeat signed with its certificate", r, {200})

    signed = node_auth.sign_request(a.key, a.node_id, a.cert, "POST", hb_path, raw)
    r, _ = master.send("POST", hb_path, machine=a, body=body, prepared_headers=signed)
    first = r.status_code
    r, _ = master.send("POST", hb_path, machine=a, body=body, prepared_headers=signed)
    results.check("T441", f"replayed heartbeat (first answered {first})", r, {401})

    stale = node_auth.sign_request(a.key, a.node_id, a.cert, "POST", hb_path, raw,
                                   now=int(time.time()) - 120)
    r, _ = master.send("POST", hb_path, machine=a, body=body, prepared_headers=stale)
    results.check("T441", "signature two minutes old", r, {401})

    stranger = node_auth._ed25519().Ed25519PrivateKey.generate()
    foreign = node_auth.issue_certificate(stranger, a.node_id, a.public_key, "elsewhere")
    r, _ = master.send("POST", hb_path, machine=a, body=body, cert=foreign)
    results.check("T441", "certificate from another master", r, {401})

    b = Machine(data_dir, "fleet-sim-impostor", 9000)
    extra.append(b)
    if enrol(master, b, results, approve=True, phase="checks-enrol"):
        r, _ = master.send("POST", hb_path, machine=b, body=body)
        results.check("T441", "B signs a heartbeat for A's node id", r, {403})
        borrowed = node_auth.sign_request(b.key, a.node_id, a.cert, "POST", hb_path, raw)
        r, _ = master.send("POST", hb_path, machine=b, body=body, prepared_headers=borrowed)
        results.check("T441", "B's key with A's certificate", r, {401})
    else:
        results.error("checks", "the second machine could not join; impersonation not checked")

    r, _ = master.send("GET", f"{API}/sync/pull", machine=a, sign=False, query={"entities": "rules"})
    results.check("T441", "unsigned data pull", r, {401})
    r, _ = master.send("GET", f"{API}/sync/pull", machine=a, query={"entities": "rules"})
    results.check("T441", "signed data pull", r, {200})

    c = Machine(data_dir, "fleet-sim-waiting", 9001)
    extra.append(c)
    r, _ = master.send("POST", REGISTER, machine=c, enrolment=True, body={
        "node_id": c.node_id, "node_name": c.name, "node_mode": "client",
        "hardware_profile": c.profile(), "client_port": 9, "public_key": c.public_key})
    results.check("T441", "a new machine waits for approval", r, {202})
    own = node_auth.issue_certificate(c.key, c.node_id, c.public_key, c.node_id)
    r, _ = master.send("POST", f"{API}/nodes/{c.node_id}/heartbeat", machine=c, body=body, cert=own)
    results.check("T441", "a waiting machine with a certificate it made itself", r, {401})

    # T438: operator routes from another address, without the install key.
    for method, path, payload in (
        ("GET", f"{API}/config", None),
        ("GET", f"{API}/approvals/pending", None),
        ("GET", f"{API}/broadcast/status/fleet-sim", None),
        ("GET", f"{API}/enrolments", None),
        ("POST", f"{API}/ask-family", {}),
        ("POST", f"{API}/route-inference", {}),
        ("POST", f"{API}/sync/manual", {"direction": "fleet-sim-probe"}),
        ("GET", f"{API}/nodes", None),
        ("GET", f"{API}/debug/nodes", None),
        ("GET", f"{API}/backups", None),
        ("POST", f"{API}/test-connection", {}),
        # A node id no master has: harmless even if the guard let it through.
        ("DELETE", f"{API}/nodes/fleet-sim-no-such-node", None),
        ("POST", f"{API}/nodes/fleet-sim-no-such-node/test", None),
    ):
        r, _ = master.send(method, path, machine=a, sign=False, body=payload)
        results.check("T438", f"{method} {path.replace(API, '')} from another host", r, {401, 403})

    # T439: minting a fleet key from another host.
    r, _ = master.send("POST", f"{API}/config/generate-key", machine=a, sign=False)
    if not results.check("T439", "new fleet key from another host", r, {401, 403}):
        print("!! The master minted a fleet key for another host: generate a new one in "
              "Settings → Interconnector.", file=sys.stderr)

    # T442: directives. The probe is a name the master does not act on.
    probe = {"directive": "fleet_sim_probe", "reason": "fleet simulator check"}
    r, _ = master.send("POST", f"{API}/receive-directive", machine=a, sign=False, body=probe)
    results.check("T442", "unsigned directive", r, {401})
    r, _ = master.send("POST", f"{API}/receive-directive", machine=a, body=probe)
    results.check("T442", "directive signed by a client", r, {403})
    return extra


# ---- report ----------------------------------------------------------------------

def _ms(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def report(results: Results, joined: int, total: int, started: float) -> bool:
    print(f"\nMachines with a certificate: {joined}/{total}   wall time {time.time() - started:.1f}s\n")
    if results.timings:
        print(f"{'phase':<18}{'calls':>7}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}   statuses")
        for phase, values in results.timings.items():
            statuses = ", ".join(f"{k}x{v}" for k, v in sorted(results.statuses[phase].items(), key=str))
            print(f"{phase:<18}{len(values):>7}{statistics.median(values):>10.1f}"
                  f"{_ms(values, 0.95):>10.1f}{max(values):>10.1f}   {statuses}")
    if results.checks:
        print("\nSecurity checks")
        for task, what, status, expected, ok, detail in results.checks:
            mark = "PASS" if ok else "FAIL"
            note = f"   ({detail})" if detail else ""
            print(f"  {mark}  {task}  {what}: HTTP {status}, expected {expected}{note}")
    if results.failures:
        print(f"\nUnexpected answers ({len(results.failures)}):")
        for line in results.failures[:25]:
            print(f"  {line}")
        if len(results.failures) > 25:
            print(f"  ... {len(results.failures) - 25} more")
    clean = not results.failures and all(check[4] for check in results.checks)
    print("\nRESULT:", "clean" if clean else "NOT clean")
    return clean


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--master", default=os.environ.get("GX_MASTER_URL", "http://127.0.0.1:5000"),
                        help="master URL (default http://127.0.0.1:5000, or GX_MASTER_URL)")
    parser.add_argument("--nodes", type=int, default=1, help="simulated machines (1 to 1000)")
    parser.add_argument("--approve", action="store_true",
                        help="approve each machine as the operator (otherwise they wait)")
    parser.add_argument("--fleet-key", default=os.environ.get("GX_FLEET_KEY"),
                        help="the master's fleet key (or GX_FLEET_KEY)")
    parser.add_argument("--api-key", default=os.environ.get("GUAARDVARK_API_KEY"),
                        help="the master's install API key, for operator calls (or GUAARDVARK_API_KEY)")
    parser.add_argument("--beats", type=int, default=2, help="heartbeats per machine (default 2)")
    parser.add_argument("--concurrency", type=int, default=32, help="machines at once (default 32)")
    parser.add_argument("--checks", action="store_true",
                        help="run the S1 security checks (always on with --nodes 1)")
    parser.add_argument("--evidence-path", help="route each machine posts evidence to, when the master has one")
    parser.add_argument("--decide-path", help="route each machine sends a decide request to, when the master has one")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".guaardvark" / "fleet_sim",
                        help="where the simulated machines keep their keys (default ~/.guaardvark/fleet_sim)")
    parser.add_argument("--keep", action="store_true", help="leave the simulated machines enrolled")
    args = parser.parse_args(argv)
    if not 1 <= args.nodes <= 1000:
        parser.error("--nodes must be between 1 and 1000")

    master = Master(args.master, args.fleet_key, args.api_key)
    try:
        status = requests.get(f"{master.url}{API}/status", timeout=TIMEOUT_S).json()["data"]["status"]
    except Exception as exc:
        print(f"Cannot reach the master at {master.url}: {exc}", file=sys.stderr)
        return 1
    if not status.get("is_enabled") or status.get("node_mode") != "master":
        print(f"{master.url} is not an enabled Interconnector master ({status}).", file=sys.stderr)
        return 1

    args.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(args.data_dir, 0o700)
    machines = [Machine(args.data_dir, f"fleet-sim-{i:04d}", i) for i in range(args.nodes)]
    results = Results()
    started = time.time()
    print(f"Simulating {args.nodes} machine(s) against {master.url} "
          f"({args.concurrency} at once, {args.beats} heartbeat(s) each)")

    joined = 0
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(run_machine, master, m, results, args): m for m in machines}
        for future in as_completed(futures):
            try:
                joined += bool(future.result())
            except Exception as exc:
                results.error("machine", f"{futures[future].name}: {exc}")

    extra = []
    if (args.nodes == 1 or args.checks) and machines[0].cert:
        extra = security_checks(master, machines[0], args.data_dir, results, args)
    elif args.nodes == 1 or args.checks:
        results.error("checks", "the first machine holds no certificate; run with --approve")

    if not args.keep:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            for future in [pool.submit(remove, master, m, results) for m in machines + extra]:
                future.result()

    if not args.approve and joined == 0:
        print("\nThe machines wait for approval; approve them in Settings → Interconnector "
              "(Machines joining the fleet) or run again with --approve.")
    return 0 if report(results, joined, len(machines), started) else 1


if __name__ == "__main__":
    sys.exit(main())
