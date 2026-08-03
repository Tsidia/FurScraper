# PyInstaller build definition for FurScraper.
#
# Produces a single windowed FurScraper.exe with the Python runtime, tkinter and
# requests bundled in, so the user installs nothing but Docker (and only if they
# want the FurAffinity modules).
#
# Build with:  pyinstaller --clean --noconfirm furscraper.spec

block_cipher = None

a = Analysis(
    ['furscraper.py'],
    pathex=[],
    binaries=[],
    # The web UI's static assets, resolved at runtime via sys._MEIPASS.
    datas=[('ui', 'ui')],
    # Modules reached only through the dispatcher or by name, which the static
    # analyser can miss.
    hiddenimports=[
        'webapp',
        'scraper',
        'scheduler',
        'gallery',
        'dialogs',
        'netutil',
        'pairing',
        'tsidia_hub',
        'modules.base',
        'modules.e621_mod',
        'modules.fa_artists',
        'modules.fa_common',
        'modules.fa_search',
        'modules.fa_site',
        'modules.fa_watchlist',
        # Imported inside functions (optional at runtime), which the static
        # analyser can miss; zeroconf needs ifaddr at runtime.
        'zeroconf',
        'ifaddr',
        'qrcode',
        'qrcode.image.svg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here is used at runtime; dropping them keeps the binary smaller.
    # tkinter in particular is worth ~5MB and the UI no longer needs it.
    excludes=['pytest', 'setuptools', 'pip', 'tkinter', '_tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='FurScraper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Windowed: no console flashes when the scheduled task fires. Startup errors
    # still surface, via the handler in furscraper.py.
    console=False,
    icon='furscraper.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
