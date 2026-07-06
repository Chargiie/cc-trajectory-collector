---
name: serp-image-search
description: >-
  全网图片搜索（StepFun SERP，免 key）。给关键词返回真实网页图片候选（含
  width/height/来源/原图 URL），可按分辨率筛、下载到本地。适合旅游攻略配图找真实照片，
  尤其境外目的地（amap 无覆盖时）。返回后务必下载到本地再在 HTML 引用（规避 CORS）。
---

# SERP 图搜（全网真实图片）

打 StepFun SERP 图搜，给关键词拿真实网页图片候选（不限授权，来自全网）。**免 API key**（但 `search` 需配 SERP 服务地址，见下「端点配置」）。

## 端点配置（必读）
`search` 通过环境变量 **`STEPSEARCH_SERP_URL`** 拿 SERP 服务地址：
```bash
export STEPSEARCH_SERP_URL=https://<你的-serp-host>/v1/serp_image_search
```
- **未设置** → `search` 干净退出（退出码 3），**图搜链自动降级到 amap POI 图**，不报错崩流程。
- `download` 只需图片 URL，**不依赖**此变量。
- 内网数据同学：在跑批 shell（或 `~/.zshrc`）里 `export STEPSEARCH_SERP_URL=<内网 SERP 端点>`，采集器会把该 env 继承给内层 cc，图搜链即启用 SERP。

## 用法
```bash
# 搜（封面用 --min-long-edge 1200 挑大图；默认返回按分辨率降序 top5）
python3 <serp-image-search 目录>/scripts/serp_image_search.py search "<关键词>" --min-long-edge 1200 --top 5
python3 <serp-image-search 目录>/scripts/serp_image_search.py search "<关键词>" --format json   # 结构化
# 下载（浏览器 UA，规避防盗链）
python3 <serp-image-search 目录>/scripts/serp_image_search.py download "<origin_image_url>" -o workspace/img/xxx.jpg
```
返回：`source / snippet / width / height / origin_image_url / doc_url / rank`。

## 配图纪律
- **每关键词只返回 5 张**，多页攻略按点位/景点分多次搜。
- **封面/满版**挑长边 ≥1200px；内页不设门槛。
- **必须下载到本地**（`workspace/img/`）再用相对路径引用——别 hot-link（CORS 会破图）。
- 图源是全网网页图（非 CC），个人行程参考可用；对外正式分发注意版权。
