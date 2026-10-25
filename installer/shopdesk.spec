# PyInstaller recipe for ShopDesk (run through BUILD_INSTALLER.bat)
import os
from PyInstaller.utils.hooks import collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(root, "main.py")],
    pathex=[root],
    datas=[(os.path.join(root, "app", "schema.sql"), "app"), (os.path.join(root, "assets"), "assets")],
    hiddenimports=collect_submodules("app"),
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "pandas", "PIL",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ShopDesk", console=False,
          icon=os.path.join(root, "assets", "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ShopDesk")
