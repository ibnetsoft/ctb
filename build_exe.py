import os
import subprocess
import shutil

# 1. 이전 빌드 정리
for folder in ['build', 'dist']:
    if os.path.exists(folder):
        shutil.rmtree(folder)
if os.path.exists('MMBot.spec'):
    os.remove('MMBot.spec')

# 2. PyInstaller 명령 구성
# --onefile: 단일 파일
# --add-data: index.html 포함 (Windows 세미콜론 사용)
# --icon: 아이콘 설정
# --name: 실행 파일 이름
cmd = [
    'python', '-m', 'PyInstaller',
    '--onefile',
    '--add-data', 'index.html;.',
    '--icon', 'icon.ico',
    '--name', 'MMBot',
    'main.py'
]

print(f"Executing: {' '.join(cmd)}")
subprocess.run(cmd, check=True)

print("\nBuild completed! The executable is in the 'dist' folder.")
