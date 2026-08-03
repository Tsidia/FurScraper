"""Network helpers: LAN address discovery and the Windows Firewall rule.

LAN access is opt-in. Once the server binds every interface, inbound
connections still have to clear Windows Defender Firewall, and a user who
dismisses the connection prompt gets silent, mystifying failures. So the rule
is managed deliberately: added in a single elevated PowerShell run (one UAC
prompt, taken when the user explicitly enables network access), and verified
afterwards with an unelevated query, because `Start-Process -Verb RunAs`
cannot report the inner command's outcome reliably.

The rules are program-based rather than port-based so a port change in the
config does not need a second elevation.
"""
import base64
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

RULE_NAME = "Tsidia FurScraper"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_ips_lock = threading.Lock()
_ips_cache = {"at": 0.0, "ips": []}


def lan_ips(max_age=60):
    """This machine's LAN IPv4 addresses, the default-route one first.

    The UDP-connect trick never sends a packet; it just asks the routing table
    which local address would be used. getaddrinfo on our own hostname then
    picks up any further interfaces (second NIC, VPN, etc.).
    """
    with _ips_lock:
        if time.time() - _ips_cache["at"] < max_age:
            return list(_ips_cache["ips"])
    ips = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: routable, never answered
        ip = s.getsockname()[0]
        if not ip.startswith("127."):
            ips.append(ip)
    except OSError:
        pass
    finally:
        s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith(("127.", "169.254.")):
                ips.append(ip)
    except OSError:
        pass
    with _ips_lock:
        _ips_cache["at"] = time.time()
        _ips_cache["ips"] = list(ips)
    return ips


def primary_ip():
    ips = lan_ips()
    return ips[0] if ips else None


# ---------- Windows Firewall ----------

def _programs():
    """The executables that actually listen. Frozen builds are one exe; from
    source it is python.exe, plus pythonw.exe which the scheduled task uses."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    exe = Path(sys.executable).resolve()
    programs = [str(exe)]
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        programs.append(str(pythonw.resolve()))
    return programs


def _ps(command, timeout=30):
    p = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def rule_status():
    """'ok' when every listening program has one of our allow rules, 'missing'
    when at least one does not (including after the exe was moved, since the
    rule names a path), 'unknown' when the query itself failed."""
    try:
        rc, out, _ = _ps(
            f"Get-NetFirewallRule -DisplayName '{RULE_NAME}*' -ErrorAction SilentlyContinue"
            " | Get-NetFirewallApplicationFilter"
            " | Select-Object -ExpandProperty Program"
        )
    except Exception:
        return "unknown"
    if rc != 0:
        return "unknown"
    have = {line.strip().lower() for line in out.splitlines() if line.strip()}
    needed = {p.lower() for p in _programs()}
    return "ok" if needed <= have else "missing"


def add_rules():
    """Replace our rules in one elevated run. Returns the resulting status:
    'ok', 'declined' when the UAC prompt was refused, or 'failed'.

    Private and Domain profiles only: exposing a personal gallery on networks
    Windows itself considers Public would be the wrong default, and the UI
    explains this rather than working around it.
    """
    commands = [
        f"Remove-NetFirewallRule -DisplayName '{RULE_NAME}*' -ErrorAction SilentlyContinue"
    ]
    for i, program in enumerate(_programs()):
        name = RULE_NAME if i == 0 else f"{RULE_NAME} ({Path(program).stem})"
        commands.append(
            f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound"
            f" -Action Allow -Protocol TCP -Program '{program}'"
            " -Profile Private,Domain | Out-Null"
        )
    # -EncodedCommand sidesteps the quoting of a command inside a command
    # inside a command, which plain -ArgumentList strings cannot survive.
    encoded = base64.b64encode("; ".join(commands).encode("utf-16-le")).decode("ascii")
    outer = (
        "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden"
        f" -ArgumentList '-NoProfile','-EncodedCommand','{encoded}'"
    )
    try:
        rc, out, err = _ps(outer, timeout=180)
    except Exception:
        return "failed"
    if rc != 0:
        # Refusing the UAC prompt surfaces as "The operation was canceled".
        return "declined" if "cancel" in (err + out).lower() else "failed"
    return "ok" if rule_status() == "ok" else "failed"
