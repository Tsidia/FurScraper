"""Native message boxes via ctypes.

The UI is a web page and background runs are headless, so tkinter would
otherwise be pulled into the build for nothing more than the occasional error
dialog. user32.MessageBoxW costs nothing and is always present on Windows.
"""
import sys

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000


def _show(title, message, flags):
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, str(message), str(title), flags | MB_SETFOREGROUND | MB_TOPMOST
        )
        return True
    except Exception:
        # Not Windows, or no window station (a service-like context). Falling
        # back to stderr at least leaves a trace when run from a console.
        try:
            sys.stderr.write(f"{title}: {message}\n")
        except Exception:
            pass
        return False


def error(title, message):
    return _show(title, message, MB_OK | MB_ICONERROR)


def warn(title, message):
    return _show(title, message, MB_OK | MB_ICONWARNING)
