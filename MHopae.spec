# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['source/app.py'],
    pathex=[str(__import__("pathlib").Path(SPECPATH) / "source")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'scipy', 'matplotlib', 'torch', 'tensorflow', 'cv2', 'IPython', 'pytest', 'sympy', 'lxml', 'PIL.AvifImagePlugin', 'PIL.WebPImagePlugin', 'PIL.ImageCms', 'PIL._avif', 'PIL._webp', 'PIL._imagingcms'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MHopae',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MHopae',
)
