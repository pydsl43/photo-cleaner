# Photo Cleaner

智能照片清理工具 — 检测相似照片、按日期/地点归类整理。

## 功能

- **相似照片检测** — 用感知哈希算法找出相似照片，一键清理
- **智能保留策略** — 可选保留最大文件 / 最清晰 / EXIF 最完整
- **归类整理** — 按拍摄日期或 GPS 地点自动归类
- **桌面原生窗口** — 双击 exe 直接打开，无需浏览器

## 下载

去 [Releases](https://github.com/pydsl43/photo-cleaner/releases) 页面下载最新版本。

## 构建

```bash
pip install -r requirements.txt
pyinstaller build.spec --noconfirm
```
