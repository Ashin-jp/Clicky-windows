"""
build.py — PyInstaller Build Script

Builds Windows Clicky into a standalone .exe.
Run: python build.py
"""

import PyInstaller.__main__
import os

script_dir = os.path.dirname(os.path.abspath(__file__))

PyInstaller.__main__.run([
    os.path.join(script_dir, "main.py"),
    "--name=Clicky",
    "--onefile",
    "--windowed",
    "--noconfirm",
    "--clean",
    f"--distpath={os.path.join(script_dir, 'dist')}",
    f"--workpath={os.path.join(script_dir, 'build')}",
    # Add data files
    f"--add-data={os.path.join(script_dir, 'assets')}:assets",
    # Exclude conflicting Qt bindings
    "--exclude-module=PyQt5",
    "--exclude-module=PyQt6",
    # Hidden imports for runtime
    "--hidden-import=PySide6.QtSvg",
    "--hidden-import=sounddevice",
    "--hidden-import=pygame",
    "--hidden-import=mss",
    "--hidden-import=websockets",
])

print("\\n✅ Build complete! Output: dist/Clicky.exe")
