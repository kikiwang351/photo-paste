# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['photo_paste.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('照片黏貼A4大圖範本.docx', '.'),
        ('照片黏貼一頁雙圖範本.docx', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy','scipy','matplotlib','pandas','PyQt5','PyQt6','PySide2','PySide6','IPython','pytest','setuptools'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='photo-helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
