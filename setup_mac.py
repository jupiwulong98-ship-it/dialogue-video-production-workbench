"""仅为当前 Mac 生成开发启动壳，依赖项目目录和虚拟环境，不冒充可分发安装包。"""
from setuptools import setup
from pathlib import Path

setup(
    name='对话视频工具',
    version='0.1.0',
    app=[{'script': str(Path(__file__).with_name('mac_launcher.py')), 'dest_base': '对话视频工具'}],
    options={'py2app': {
        'alias': True,
        'argv_emulation': False,
        'plist': {'CFBundleName': '对话视频工具', 'CFBundleDisplayName': '对话视频工具',
                  'CFBundleIdentifier': 'local.longshen.dialogue-tool', 'LSUIElement': False},
    }},
)
