"""
PyInstaller 打包脚本
生成的 exe 需要放在 data 文件夹同级目录使用
"""
import PyInstaller.__main__
import os

code_dir = os.path.dirname(os.path.abspath(__file__))

PyInstaller.__main__.run([
    os.path.join(code_dir, 'run.py'),
    '--onefile',
    '--name=RemoteControl',
    '--console',
    '--noconfirm',
    '--hidden-import=uvicorn',
    '--hidden-import=uvicorn.logging',
    '--hidden-import=uvicorn.loops',
    '--hidden-import=uvicorn.loops.auto',
    '--hidden-import=uvicorn.protocols',
    '--hidden-import=uvicorn.protocols.http',
    '--hidden-import=uvicorn.protocols.http.auto',
    '--hidden-import=uvicorn.protocols.websockets',
    '--hidden-import=uvicorn.protocols.websockets.auto',
    '--hidden-import=uvicorn.lifespan',
    '--hidden-import=uvicorn.lifespan.on',
    '--hidden-import=fastapi',
    '--hidden-import=starlette',
    '--hidden-import=websockets',
    '--hidden-import=cv2',
    '--hidden-import=numpy',
    '--hidden-import=aiortc',
    '--hidden-import=dxcam',
    '--hidden-import=pyperclip',
    '--hidden-import=requests',
    '--hidden-import=PIL',
    '--hidden-import=PIL.Image',
    '--hidden-import=psutil',
    '--uac-admin',
    f'--distpath={os.path.dirname(code_dir)}',
])

print("\n" + "="*50)
print("打包完成!")
print("="*50)
print(f"exe 位置: {os.path.join(os.path.dirname(code_dir), 'RemoteControl.exe')}")
print("\n使用方法:")
print("1. 将 RemoteControl.exe 和 data 文件夹放在同一目录")
print("2. 运行 RemoteControl.exe")
print("="*50)
