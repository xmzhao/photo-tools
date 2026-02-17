# 媒体文件处理脚本集

该仓库包含一组面向照片/视频的命令行脚本，主要能力包括：
- 推断媒体文件最可能的创建时间
- 按日期自动整理照片/视频目录
- 读取照片 GPS 并反查中文 POI
- 统计目录内媒体数量及 POI 热点
- 批量重命名特定日期格式的文件夹

## 目录结构

- `media_creation_time.py`：单文件时间推断
- `photo_organizer.py`：按日期整理媒体
- `photo_gps_to_poi.py`：GPS 反查 POI
- `count_media_files.py`：目录媒体计数 + POI 投票
- `pic_proc.py`：旧目录名规则批量重命名
- `common/`：公共模块（时间解析、GPS、逆地理、扩展名常量等）

## 环境要求

- Python 3.10+
- 推荐安装命令行工具：
  - `exiftool`（读取图片/视频元数据）
  - `ffprobe`（来自 ffmpeg，读取视频元数据）
- 可选 Python 依赖：
  - `Pillow`（当 exiftool 无法读取图片 GPS 时作为兜底）

### macOS 安装示例

```bash
brew install exiftool ffmpeg
python3 -m pip install pillow
```

## 支持的媒体类型

- 图片：`jpg/jpeg/png/heic/heif/gif/bmp/tif/tiff/webp/dng/raw/arw/cr2/cr3/nef/orf/rw2`
- 视频：`mp4/mov/m4v/avi/mkv/3gp/mts/m2ts/mpg/mpeg/wmv/webm`
- Sidecar（仅整理脚本使用）：`aae/xmp`

## 脚本说明与用法

### 1) 推断文件创建时间

文件：`media_creation_time.py`

用途：综合 `exiftool` / `ffprobe` / 文件系统时间 / 路径日期信息，给出“最可能创建时间”。

```bash
python3 media_creation_time.py /path/to/media.jpg
python3 media_creation_time.py /path/to/media.mp4 --json
```

说明：
- 默认文本输出包含最可能时间、来源和排序后的候选时间。
- `--json` 输出完整候选结构，便于程序集成。

### 2) 按日期整理媒体目录

文件：`photo_organizer.py`

用途：扫描输入目录并按估计日期归档到输出目录，目录格式为：
- 季度目录：`YYYYQn`
- 日期目录：`YYYYMMDD`

```bash
# 仅预演，不复制/移动
python3 photo_organizer.py --input-dir /path/in --output-dir /path/out --mode dry_run

# 复制文件
python3 photo_organizer.py --input-dir /path/in --output-dir /path/out --mode copy

# 移动文件
python3 photo_organizer.py --input-dir /path/in --output-dir /path/out --mode move
```

说明：
- 会在当前工作目录生成日志：
  - `skipped_files.log`：目标已存在或冲突而跳过的文件
  - `copied_files.log`：已复制/移动（或 dry-run 计划）文件
- 同一轮运行中若两个源文件映射到同一目标路径：先处理到的文件会被复制/移动，后处理到的文件会被跳过并记录到 `skipped_files.log`。
- `.aae` / `.xmp` 会尝试复用同名主文件的日期键。

### 3) 照片 GPS 反查 POI

文件：`photo_gps_to_poi.py`

用途：读取照片 EXIF GPS，经高德/天地图逆地理编码输出中文地址与最近 POI。

```bash
# 高德
python3 photo_gps_to_poi.py /path/to/photo.jpg --provider amap --amap-key <AMAP_KEY>

# 天地图
python3 photo_gps_to_poi.py /path/to/photo.jpg --provider tianditu --tianditu-key <TIANDITU_KEY>

# JSON 输出
python3 photo_gps_to_poi.py /path/to/photo.jpg --provider amap --amap-key <AMAP_KEY> --json
```

说明：
- 输入照片需包含可用 GPS 信息。
- 若未提供对应 Key，会直接报错退出。

### 4) 统计目录媒体数量和 POI 热点

文件：`count_media_files.py`

用途：递归扫描目录，统计图片/视频数量，并对含 GPS 的媒体做城市-POI 投票（每目录输出 Top2）。

```bash
# 使用环境变量中的 AMAP_KEY
export AMAP_KEY=your_key
python3 count_media_files.py /path/to/root

# 指定排序并输出 JSON
python3 count_media_files.py /path/to/root --provider amap --amap-key <AMAP_KEY> --sort-by media_total --json
```

可选参数：
- `--provider`: `amap` 或 `tianditu`
- `--amap-key`: 高德 key（默认读取 `AMAP_KEY`）
- `--tianditu-key`: 天地图 key（默认读取 `TIANDITU_KEY`）
- `--sort-by`: `path` 或 `media_total`
- `--json`: JSON 格式输出

### 5) 旧目录名批量重命名

文件：`pic_proc.py`

用途：将子目录名中形如 `Month DD, YYYY` 的日期提取后，重命名为：
`YYYYMMDD.<原目录名>`。

```bash
python3 pic_proc.py /path/to/parent_dir
```

注意：
- 脚本会直接执行重命名，无 dry-run。
- 仅处理一级子目录。

## 常见问题

- 缺少 `exiftool` / `ffprobe`：先安装后再运行（尤其 `photo_organizer.py`）。
- GPS 为空：原图可能无 EXIF GPS，或元数据被清理。
- 逆地理失败：检查网络和 API Key 是否有效。

## 许可证

当前仓库未包含独立 License 文件；如需开源发布，建议补充许可证声明。
