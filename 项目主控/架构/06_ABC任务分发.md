# A/B/C/D 任务分发与合并计划

> 日期：2026-11-15
> 基线：`main` @ `5aa64d3`（A/B/C/D 路线图已提交）
> 原则：一任务一分支一子 agent；只在各自 worktree 工作；不切 main、不改别人的分支；完成即 commit + push。

---

## 1. 分支/工作树/负责人

| 任务 | 分支 | 工作树 | 负责人 | 交付物 |
|---|---|---|---|---|
| A 点选检测源 | `tool/star-pick` | `_worktrees/star-pick` | `star-pick-owner` | ✅ 源列表 API + canvas 点选 + 测试（`10ac26c`，已合并） |
| B 星表服务 | `tool/catalog` | `_worktrees/catalog` | `catalog-owner` | ✅ 星表服务 + RUWE/2MASS-WISE + persist（`e68b029`，DB 126 tests；已合并） |
| C 任意天区设计 | `tool/anyfield` | `_worktrees/anyfield` | `anyfield-owner` | ✅ 设计 + C-P0/P1A/P1B（本地 NPZ/FITS、S3 discover/fetch、源列表；`6d487c8`，45 tests） |
| D 数据比对设计 | `tool/identify` | `_worktrees/identify` | `identify-owner` | ✅ 设计 + D-MVP + D-C P0/P1（C 源列表→识别）+ B v2；app 268 tests |

**合并顺序**：A → B → D 设计 → C 设计 → D 实现 → C 实现。  
A/B 可并行；C/D 设计可并行；D 的实现必须等 A/B 接口稳定。

---

## 2. 接口约定（先定，避免互相等）

### A → 前端

`/api/analyze` 响应增加 `sources`：

```json
{
  "sources": [
    {
      "epoch_id": "...",
      "x": 72.3,
      "y": 58.1,
      "ra": 269.452,
      "dec": 4.693,
      "flux": 12.3,
      "flux_err": 0.4,
      "snr": 30.0,
      "saturated": false,
      "quality": []
    }
  ]
}
```

- `x/y` 用该分析结果的 common WCS 像素坐标；
- 前端按当前帧/公共帧画点；
- 点击最近源 → 取 `ra/dec` → 调 `/api/analyze`；
- 不改 DB。

### B → App

独立服务（端口 8799）或 DB 工具内新模块；API：

```http
GET /api/v1/catalog/cone?ra=&dec=&radius_deg=&epoch=&catalog=gaia
POST /api/v1/catalog/match
```

- 响应带 `release/version`；
- 失败 `CATALOG_UNAVAILABLE`，不能返回“空匹配=新星”；
- 不改 app；不改生产 `var/catalog.sqlite`，使用独立 `var/star_catalog.sqlite`。

### D 设计 → A/B

D 只写设计，不写代码；等 A/B 合并后再实现 `identify` 分类层。

---

## 3. 验收门槛

### A

- Barnard 场至少 10 个亮源可点选；
- 点击后发起分析，无 500；
- 选中源高亮，URL 带 `ra/dec/fov`；
- app 现有 111 tests 不回归 + 新增测试。

### B

- 7 个预设场至少 Barnard 能返回 Gaia 锥搜索结果；
- `catalog_release` 版本/许可字段齐全；
- 锥搜索/匹配测试通过；无网络时有 fixture；
- 不改生产 DB schema；测试用 fixture。

### C（设计）

- 覆盖：全量/按需历元索引、IRSA/S3、增量新图、缓存、API、验收、风险；
- 明确“不写大代码”，只交付设计与 spike 计划。

### D（设计）

- 覆盖：Gaia/2MASS/WISE 比对、PM/视差传播、3σ、运动残差、known/ambiguous/uncatalogued、DB 同步、API/UI、验收；
- 明确“不输出新恒星发现”。

---

## 4. 非重叠文件

| 任务 | 允许改 |
|---|---|
| A | `系统/app/core/pipeline.py`、`系统/app/server.py`、`系统/app/web/app.js`、`系统/app/tests/` |
| B | `系统/数据库工具/db/`、`系统/数据库工具/catalog_server.py`、`系统/数据库工具/tools/`、`系统/数据库工具/tests/`、`系统/数据库工具/docs/` |
| C | `项目主控/架构/06_C_任意天区与增量设计.md` |
| D | `项目主控/架构/07_已知未知识别与数据比对设计.md` |

> 若确实需要跨文件，先在自己的分支说明，不要直接改 main 或其他 worktree。

---

## 5. 当前状态

- 路线图：已提交 `5aa64d3`。
- 任务分发：本文件提交后，A/B/C/D 子 agent 启动。
- 合并由 lead 负责；每个分支 push 后由 lead 跑测试/T1/T6 再合并。
