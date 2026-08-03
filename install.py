"""Sets FurScraper up: installs dependencies, checks the environment, and
creates a Desktop shortcut.

Everything is done against `sys.executable` — the interpreter running this
script — and that same interpreter is baked into the shortcut. That way the
packages always land where the shortcut will look for them, which is the classic
way a "pip install worked but nothing happens" setup goes wrong.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def step(msg):
    print(f"\n==> {msg}")


def install_requirements():
    step("Installing Python dependencies")
    req = HERE / "requirements.txt"
    if not req.exists():
        print("  requirements.txt not found, skipping.")
        return True
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("  FAILED:")
        print(r.stderr or r.stdout)
        return False
    print(f"  Installed into: {sys.executable}")
    return True


def check_imports():
    """The UI is a browser page, so there is no GUI toolkit to check for; just
    confirm the app's own imports resolve in this interpreter."""
    step("Checking the app imports cleanly")
    r = subprocess.run(
        [sys.executable, "-c", "import webapp, scraper"],
        capture_output=True,
        text=True,
        cwd=str(HERE),
    )
    if r.returncode != 0:
        print("  FAILED:")
        print((r.stderr or r.stdout).strip())
        return False
    print("  OK.")
    return True


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


def make_shortcut():
    step("Creating Desktop shortcut")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    script = HERE / "furscraper.py"
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
        print("  Failed to create shortcut:")
        print(r.stderr or r.stdout)
        return False
    print(f"  Created: {shortcut}")
    return True


def main():
    print("FurScraper setup")
    print(f"Using interpreter: {sys.executable}")

    if not install_requirements():
        sys.exit(1)
    if not check_imports():
        sys.exit(1)
    if not make_shortcut():
        sys.exit(1)

    print("\nDone. Open FurScraper from the Desktop shortcut.")
    print("Enable the modules you want, then press Save & Schedule.")


if __name__ == "__main__":
    main()
