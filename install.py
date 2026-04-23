"""Creates a FurScraper shortcut on the Windows Desktop."""
import os
import sys
import subprocess
from pathlib import Path


def desktop_dir():
    # Respect OneDrive-redirected Desktop if present.
    candidates = []
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        candidates.append(Path(onedrive) / "Desktop")
    candidates.append(Path.home() / "OneDrive" / "Desktop")
    candidates.append(Path.home() / "Desktop")
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


def main():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    script = Path(__file__).resolve().parent / "config_gui.py"
    dd = desktop_dir()
    dd.mkdir(parents=True, exist_ok=True)
    shortcut = dd / "FurScraper.lnk"

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{shortcut}'); "
        f"$s.TargetPath = '{pythonw}'; "
        f"$s.Arguments = '\"{script}\"'; "
        f"$s.WorkingDirectory = '{script.parent}'; "
        f"$s.IconLocation = '{pythonw},0'; "
        "$s.Description = 'FurScraper configuration'; "
        "$s.Save()"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("Failed to create shortcut:")
        print(r.stderr or r.stdout)
        sys.exit(1)
    print(f"Shortcut created: {shortcut}")


if __name__ == "__main__":
    main()
