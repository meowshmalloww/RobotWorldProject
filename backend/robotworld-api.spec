from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

datas, binaries, hiddenimports = [], [], []
for package in ("mujoco", "glfw", "pxr", "trimesh", "opentelemetry", "openai", "uvicorn", "wgpu", "pygfx", "rendercanvas", "pylinalg"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

hiddenimports += collect_submodules("app")
hiddenimports += [
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    # MuJoCo 3.11 exposes Renderer through a guarded compatibility import.
    # PyInstaller cannot see that edge reliably, so freeze the concrete
    # implementation used by app.services.simcore.
    "mujoco.rendering.classic.renderer",
    "mujoco.rendering.classic.gl_context",
]

a = Analysis(
    [str(ROOT / "run_server.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "notebook", "pytest", "tkinter", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="robotworld-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="robotworld-api")
