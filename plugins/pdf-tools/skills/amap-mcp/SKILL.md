---
name: amap-mcp
description: >-
  通过高德地图 MCP Server 获取真实地图数据：地理编码/逆地理编码、POI 关键字与周边搜索、
  POI 详情、驾车/步行/骑行/公交路线、距离测量、天气、IP 定位，以及生成个人地图/导航/打车
  的唤端 URI。当用户要查地点坐标/地址、找店或景点、算两地怎么走与多久、查天气、做行程里的
  真实距离与通勤，或需要高德 App 唤端链接时使用。区别于 `amap`（REST 脚本版）：本版走
  高德官方 MCP Server（SSE），优先用 harness 注册的 amap MCP 工具。
---

# 高德地图 MCP Skill

用**高德官方 MCP Server** 拿真实地图数据。MCP server 以 SSE 接入:
`https://mcp.amap.com/sse?key=<KEY>`,暴露 15 个 `maps_*` 工具(见下表)。

## 两种调用路径（按环境二选一）
1. **首选——harness 已注册 amap MCP**:直接调用模型可见的 `amap` MCP 工具(命名通常为 `mcp__amap__<工具名>`),原生 tool call,无需脚本。
   - 检查是否已注册:`claude mcp list`(应看到 `amap … (SSE) - ✓ Connected`)。
   - 未注册时添加:`claude mcp add --transport sse amap "https://mcp.amap.com/sse?key=<KEY>"`。
2. **兜底——裸 shell / 子进程 / 未把 MCP 工具暴露给模型**:用同目录脚本走同一 SSE server:
   ```bash
   python3 scripts/mcp_call.py <工具名> '<json 参数>'
   python3 scripts/mcp_call.py list                 # 列出全部工具
   ```
   key 解析顺序:环境变量 `AMAP_MCP_KEY` → `scripts/key.txt` → `~/.stepfun/skills/amap/key.txt`。

## 工具清单（15 个，按官方分类；坐标统一「经度,纬度」如 `120.13,30.26`）

**坐标解析**
| 工具 | 用途 | 必填 | 可选 |
|---|---|---|---|
| `maps_geo` | 规范**地址**/地标/景区名→坐标;**POI 别用它**(易报 ENGINE_RESPONSE_DATA_ERROR) | `address` | `city` 指定查询城市 |
| `maps_regeocode` | 坐标→行政区划地址 | `location` | — |
| `maps_ip_location` | IP 定位 | `ip` | — |

**POI 搜索**
| 工具 | 用途 | 必填 | 可选 |
|---|---|---|---|
| `maps_text_search` | 关键字搜 POI,返回 `id`/`name`/`address`——**不含坐标**,要坐标须拿 `id` 接 `maps_search_detail` | `keywords` | `city`;`citylimit`(bool,是否限定城市内,默认否) |
| `maps_around_search` | 中心坐标**周边**搜 POI | `keywords` `location` | `radius`(米);`strategy`(0默认/1扫街榜) |
| `maps_search_detail` | 按 POI `id` 查详情:含 `location` 坐标、评分/营业时间/人均等 | `id` | — |

**路线规划**
| 工具 | 用途 | 必填 | 可选 |
|---|---|---|---|
| `maps_direction_walking` | 步行路线(≤100km,citywalk 排点必备) | `origin` `destination` | — |
| `maps_direction_driving` | 驾车路线 | `origin` `destination` | — |
| `maps_direction_bicycling` | 骑行路线(≤500km) | `origin` `destination` | — |
| `maps_direction_transit_integrated` | 公交/地铁/火车综合;跨城需传起终城市 | `origin` `destination` `city`(起城) `cityd`(终城) | — |

**距离测量**
| 工具 | 用途 | 必填 | 可选 |
|---|---|---|---|
| `maps_distance` | 多起点→一终点批量测距 | `origins`(多起点用 `\|` 分隔) `destination` | `type`(1驾车/0直线/3步行) |

**天气查询**
| 工具 | 用途 | 必填 | 可选 |
|---|---|---|---|
| `maps_weather` | 城市天气预报(决定室内外/带伞) | `city`(城市名或 adcode) | — |

**URI 唤起（生成高德 App 跳转 URI，不返回数据）**
| 工具 | 用途 | 必填 | 可选 |
|---|---|---|---|
| `maps_schema_personal_map` | 把行程点位按顺序生成「个人地图」展示 URI | `orgName`(小程序名) `lineList`(行程数组) | — |
| `maps_schema_navi` | 生成导航页唤端 URI | `lat` `lon`(终点) | — |
| `maps_schema_take_taxi` | 生成打车唤端 URI | `dlat` `dlon` `dname`(终点纬/经/名) | `slat` `slon` `sname`(起点,不传=当前位置) |

## 编排纪律
- **坐标先行**:要算 `direction_*` / `around_search` / `distance`,先拿到 `经度,纬度`,**别手填猜的坐标**。
  - 规范**地址** → `maps_geo` 直接出 `location`。
  - **POI/店名/景点** → `maps_text_search` 拿 `id`(它**不返回坐标**)→ `maps_search_detail` 用 `id` 取 `location`。
- **地址 vs POI**:门牌/行政区用 `maps_geo`(POI 喂 geo 易报 `ENGINE_RESPONSE_DATA_ERROR`);店名/景点名用 `maps_text_search`。
- **返回是高德原始 JSON**:`maps_weather`/`geo`/`search`/`direction` 等返回结构化 JSON,需解析后用自然语言回答或写进产物;工具级失败时 MCP 会返回形如 `API 调用失败：INVALID_PARAMS / ENGINE_RESPONSE_DATA_ERROR` 的文本。
- **失败兜底**:某次调用报错或超时 → 换工具(POI 改 `text_search`)、补全必填参数后重试;仍不行就 step-search 兜底并标注,不要编造坐标/距离。
- **唤端工具不产数据**:`maps_schema_*` 返回的是高德 App 跳转 URI(`amapuri://…`),用于"在高德里打开/导航/打车",不要当数据源。
  - 返回的 URI **原样完整展示给用户,禁止任何加工/截断**(MCP 本身也这么要求)。
  - `maps_schema_personal_map` 的 `lineList` 每个点位**必须含 `poiId`**(来自 `text_search`/`around_search`),只给 name/lon/lat 会报 `poiId不能为空`。

## 验证门
| 检查 | 方法 | 通过标准 |
|---|---|---|
| MCP 连通 | `claude mcp list` 或 `python3 scripts/mcp_call.py list` | 列出 15 个工具 / `✓ Connected` |
| 工具可用 | 跑一个真实调用(如 `maps_geo '{"address":"西湖","city":"杭州"}'`) | 返回含 `location` 的 JSON |
| 坐标链路 | `geo`(地址)或 `text_search`→`search_detail`(POI)拿坐标 → 喂 `direction_*` | 路线返回 `distance`/`duration` |
| 失败可见 | 故意缺参 | 返回明确错误文本,不静默 |

> 任何步骤 **MUST NOT** `2>/dev/null`——会吞掉 MCP 的错误文本(唯一的失败信号)。

## key 与安全
- `scripts/key.txt` 是高德 Web 服务 key,**已被 `.gitignore` 排除,切勿提交**。
- 换 key:改 `scripts/key.txt` 第一行,或设环境变量 `AMAP_MCP_KEY`,或更新 `claude mcp add` 里的 URL。
