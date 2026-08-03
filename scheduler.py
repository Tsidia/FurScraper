"""Windows Task Scheduler registration.

One task, two triggers: at log-in, and then repeating on the configured
interval. The log-in trigger is what makes the interface available as soon as
you sign in, since the scrape entry point also restarts it if it is not up.

Registration goes through an XML definition rather than schtasks' shorthand
flags. `/SC ONLOGON` on the command line registers a trigger for *any* user,
which requires administrator rights; an XML definition naming the current user
does not, and demanding elevation for this would be both rude and alarming.
"""
import getpass
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from common import APPDATA

TASK_NAME = "FurScraper"
LEGACY_UI_TASK = "FurScraper Interface"  # never shipped; removed if present
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args, timeout=60):
    p = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _current_user():
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def exec_parts(flag="--run"):
    """(command, arguments) for the task action.

    A packaged build has no interpreter or .py files to point at, so the exe
    re-invokes itself. From source we target pythonw.exe, which keeps the run
    silent: no console window, no .bat shim.
    """
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), flag
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = exe
    script = Path(__file__).resolve().parent / "furscraper.py"
    return str(pythonw), f'"{script}" {flag}'


def task_command(flag="--run"):
    """The action as one displayable string."""
    cmd, args = exec_parts(flag)
    return f'"{cmd}" {args}'


def _xml(interval_minutes):
    cmd, args = exec_parts("--run")
    user = escape(_current_user())
    start = datetime.now().replace(hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Fetches new posts from e621 and FurAffinity, and keeps the FurScraper interface available.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT{int(interval_minutes)}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(cmd)}</Command>
      <Arguments>{escape(args)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def register(interval_minutes):
    """(Re-)register the task. Raises RuntimeError with schtasks' own message,
    which is more useful than anything we could invent."""
    xml = _xml(interval_minutes)
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        # schtasks only accepts UTF-16 for /XML.
        Path(path).write_text(xml, encoding="utf-16")
        rc, out, err = _run(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/XML", path])
        if rc != 0:
            raise RuntimeError((err or out or "schtasks failed").strip())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    _run(["schtasks", "/Delete", "/F", "/TN", LEGACY_UI_TASK])
    legacy_bat = APPDATA / "run_scraper.bat"
    if legacy_bat.exists():
        try:
            legacy_bat.unlink()
        except OSError:
            pass


def unregister():
    _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])


def status():
    """Returns (registered, detail) for display. Never raises."""
    try:
        rc, out, _ = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    except Exception as e:
        return False, str(e)
    if rc != 0:
        return False, "Not scheduled yet."
    next_run = ""
    for line in out.splitlines():
        if line.lower().startswith("next run time"):
            next_run = line.split(":", 1)[1].strip()
            break
    return True, f"Next run: {next_run}" if next_run else "Scheduled."
