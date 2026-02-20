# 媒体地图浏览器（Media Map Browser）

本地优先的 Web 工具：
- 在地图上浏览本机图片/视频的拍摄位置（缩略图聚合）
- 基于地名列表做区域边界着色（全球国家 / 中国省 / 中国地级市）

## 当前功能

### 1) 媒体模式
- 世界地图渲染，支持缩放、拖拽、底图切换（标准/地形/简洁）
- 支持输入本机绝对路径扫描目录（递归）
- 支持系统目录选择器（`选择目录` 按钮）
- 解析图片/视频时间与 GPS（EXIF/元数据）
- 地图缩略图聚合展示，点击可查看分组与预览
- 无 GPS 文件进入“未定位文件”列表
- 左侧固定操作区，右侧地图区

### 2) 区域模式
- 与媒体模式采用 sheet 分页切换，不在同一页面堆叠
- 范围切换：`全球国家`、`中国.省`、`中国.地级市`
- 支持上传地名列表（txt/csv）或文本输入
- 支持地名别名匹配（含中英文、常见简称）
- 显示未匹配地名列表
- 匹配区域采用高亮 + 斜线纹理强调
- 地图内绘制中文区域名（按当前范围）

### 3) 导出
- 支持 `导出SVG` 与 `导出PNG`
- 导出包含当前区域着色、高亮纹理、中文标签
- 中国范围导出额外叠加省级轮廓线
- SVG 为矢量，适合放大与印刷

### 4) 缓存与性能
- 元数据缓存（按路径+mtime+size）
- 缩略图与预览缓存（含失败兜底）
- 扫描结果目录缓存（可刷新、勾选、批量加载、删除、清空）
- 支持同时加载多个目录缓存
- 新扫描完成后会追加到当前已加载缓存集合（不覆盖）
- 边界数据本地缓存（世界国家、中国省、中国地级市）

### 5) 交互细节
- 缩放级别支持手动输入小数（如 `4.5`）
- 浏览器标签页已配置 logo/favicon
- 预览弹窗展示路径、类型、拍摄时间、经纬度

## 目录结构

- `server.py`：后端服务（扫描、元数据、缩略图/预览、边界数据、缓存 API）
- `static/index.html`：页面结构
- `static/app.js`：前端逻辑（地图、聚合、区域着色、导出）
- `static/styles.css`：样式
- `static/assets/media-map-logo.svg`：logo/favicon
- `.cache/`：本地缓存目录

## 环境依赖

- Python 3.10+
- Python 包：`Pillow>=9.0.0`（见 `requirements.txt`）
- 推荐安装：
  - `exiftool`（读取图片/视频元数据）
  - `ffmpeg`（视频缩略图）

macOS 示例：

```bash
brew install exiftool ffmpeg
python3 -m pip install -r media-map-browser/requirements.txt
```

## 运行

在仓库根目录执行：

```bash
python3 media-map-browser/server.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

## 使用说明

### 媒体模式
1. 输入绝对路径，或点击 `选择目录`
2. 点击 `开始扫描`
3. 扫描完成后在地图查看聚合缩略图
4. 点击缩略图查看该位置媒体列表/预览
5. 在“缓存目录”中可加载历史扫描结果（支持多选）

### 区域模式
1. 点击底部 `区域模式`
2. 选择范围（全球国家 / 中国.省 / 中国.地级市）
3. 上传地名文件或输入地名列表
4. 点击 `应用地名着色`
5. 按需 `导出SVG` / `导出PNG`

## 支持的媒体格式

图片：
`jpg/jpeg/png/heic/heif/gif/bmp/tif/tiff/webp/dng/raw/arw/cr2/cr3/nef/orf/rw2`

视频：
`mp4/mov/m4v/avi/mkv/3gp/mts/m2ts/mpg/mpeg/wmv/webm`

## 本地隐私与网络说明

- 默认本地处理，不上传媒体文件到远端服务
- 地图底图来自在线瓦片服务，加载底图需联网
- 区域边界数据首次请求时会下载并缓存到本地

## 缓存位置

`media-map-browser/.cache/` 下包含：
- `meta_cache.json`：媒体元数据缓存
- `thumbs/`：缩略图缓存
- `previews/`：预览缓存
- `scans/` + `scan_index.json`：目录扫描缓存
- `boundaries/`：区域边界缓存

## 目录选择器实现

为避免 macOS 下 GUI 线程问题，服务端不使用 tkinter：
- macOS：`osascript`（AppleScript）
- Linux：`zenity` / `kdialog`
- Windows：`powershell` FolderBrowserDialog

若系统缺少目录选择器，可直接手动输入路径扫描。
