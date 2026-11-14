# 01 · MoveScope（app/）修复与加固

> 工作区根目录：`/Users/yiao/Documents/huellaX`；以下所有相对路径以此为准。

## 角色

你是 MoveScope 的前后端工程师。你的任务是修复审计中确认的 app 侧缺陷、补测试、纠文档，**不改变科学结论**，也**不接管 R1 数据接线**（那是 T2 的工作包）。

## 必读（先读，再动手）

1. `项目主控/01_验收基线与结果.md`
2. `项目主控/02_架构设计与集成契约.md`
3. `项目主控/evidence/app_defects.md`
4. `项目主控/evidence/app_5targets.json`
5. 代码：`app/server.py`、`app/core/data.py`、`app/core/pipeline.py`、`app/core/motion.py`、`app/web/app.js`、`app/web/index.html`

## 当前事实（2026-11-14 23:07 快照，23:18 复查未变）

- 5 目标：Barnard `REAL/L2/S`、Eps Ind B `REAL/L3/L`、WISE 0855 `REAL/L0/U`、RW Aur `HYBRID/L2/V`、R Dra `HYBRID/L2/V`。**这些结果必须不回归。**
- `mode=sample` 必现 HTTP 500：`cannot convert float NaN to integer`。
- `fov=-0.2` → HTTP 500 `high - low < 0`；`fov=0` 静默 24 px。
- BIC 面板 `#modelsWrap` 始终 `hidden`；`#epochList` 从不填充。
- `pipeline.run_pipeline()` 用 `result["quality"] = quality_flags(track)` 覆盖了 `motion.analyze_track()` 追加的 `BLEND_SUSPECTED` / `BLEND_INDUCED_WANDER` / `IMPLAUSIBLE_PM`。
- `RESULTS` 只增不减；每次分析 1.85–4.75 MB PNG；缓存键不含处理版本；`np.savez` 非原子；坏缓存永久 500。
- `target_id` + 冲突 `ra/dec` 被静默忽略。
- app 无自动化测试（`app/tests/` 不存在）。
- 端口默认 8765，README 写 8790。

## 目标（Definition of Done）

1. UI 暴露的所有模式不再返回 500；非法参数返回 400。
2. 研究者模式能看见 BIC 模型与历元列表。
3. 质量标记只累加、不覆盖；断网/缓存/并发不会把服务打挂。
4. 新增可运行的 unittest 测试；5 目标结果与快照一致。
5. 文档不再出现与实测不符的数字或“任意坐标已支持真实数据”的过度声明。

## 任务

### T1. 修复 `mode=sample` 500

- 目标：`POST /api/analyze {"target_id":"barnard","mode":"sample","fov":0.25}` 不再 500。
- 允许两种实现，二选一：
  - A. 返回 200：`mode=sample`、`class=U`、`readiness=L0`、`note` 明确“两份离线样本来自不同天区，只用于解析/渲染核验，不做目标测量”。
  - B. 返回 422：`{"error":{"code":"SAMPLE_MODE_NOT_FOR_TARGET","message":"..."}}`，UI 显示可读提示。
- 不允许：把样本强行当成 Barnard 测量；用 NaN 继续走 pipeline。
- 验收：curl 返回 200 或 422；0.1 s 级；无 traceback。

### T2. FOV 参数校验

- 合法范围：`0.02 ≤ fov ≤ 3.5`（与文档一致；若你决定不同范围，必须同步改文档与 UI）。
- 非法：返回 400，JSON `{"error":{"code":"INVALID_FOV","message":"..."}}`，不要 500。
- `fov=0`、负数、NaN、超大值全部覆盖；UI 在提交前也要提示。
- 超大 FOV 要有保护：不得在单请求中分配 > 300 MB（可在 400 或降采样中选一个，并写清楚）。

### T3. BIC 面板与历元列表可见

- `#modelsWrap` 在 `result.models` 非空时移除 `hidden`；为空时保持隐藏。
- `#epochList` 渲染每期 `date_obs / band / obsid / mode`；点击可跳帧（若时间不够，至少可见）。
- “研究者模式”开关应真正控制科研细节（BIC、match_stats、quality），普通模式隐藏。
- 验收：用 DOM 检查或截图证明 `#modelsWrap` 不再是 `hidden`，且 `#epochList` 有子节点。

### T4. 质量标记累加

- `motion.analyze_track()` 产出的 quality 与 `quality_flags(track)` 合并去重，不得覆盖。
- 保证 `SATURATED_SOURCE`、`FEW_EPOCHS`、`SHORT_BASELINE`、`LARGE_CENTROID_ERROR`、`BLEND_SUSPECTED`、`BLEND_INDUCED_WANDER`、`IMPLAUSIBLE_PM` 都能出现在 API/UI。
- 加单元测试：构造一个 track 或直接测合并函数，断言两类标记都在。

### T5. 缓存可靠性

- 缓存键加入 `release/version`（处理版本）；不同版本不得复用同一 NPZ。
- 写入原子化：临时文件 + `os.replace`；失败清理临时文件。
- 读取损坏 NPZ：捕获异常，删除/忽略并重取；不得永久 500。
- 加测试：旧版本缓存不复用、损坏文件可恢复、并发读不崩。

### T6. 结果内存上限

- `RESULTS` 加 LRU/TTL（例如最多 8–16 个 analysis，或总 PNG 字节上限 256 MB）。
- 淘汰时释放对应 PNG；`/api/health.results_cached` 不应无限增长。
- 加测试：连续分析超过上限后，缓存数不超上限；已淘汰的 `analysis_id` 返回 404 而不是 500。

### T7. `target_id` 与 `ra/dec` 冲突

- 若同时提供 `target_id` 与 `ra/dec`，且与目标坐标差 > 1e-6°：返回 400 `TARGET_COORD_CONFLICT`，或明确采用 override 并写进 `target` 响应。
- 推荐 400，避免嵌入方静默拿到错误目标。
- 若差值 ≤ 1e-6°：按预设处理。
- 加测试；前端仍按现有方式发送 `custom`，不需要改交互。

### T8. 补测试

- 新建 `app/tests/`（`__init__.py` + `test_*.py`），使用 `unittest`（环境没有 pytest）。
- 至少覆盖：sample、FOV、quality 合并、缓存键/损坏恢复、LRU、target 冲突。
- 不要求测试联网；用 monkeypatch/临时目录。
- 运行命令：
  ```bash
  cd /Users/yiao/Documents/huellaX
  PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
  $PY -m unittest discover -s app/tests -v
  ```

### T9. 文档纠偏

- 修正 `README.md` 与 `docs/02_实现与实测记录.md`：
  - 不再写“任意 RA/Dec 已支持真实数据”（在 T2 完成前，明确“自定义坐标当前为 SIM 演示”）；
  - 匹配星数/RMS 改为实测口径（10–21 颗、0.108–0.784 px）；
  - Barnard 0.2° 与 0.25° 的 μ 分开写（0.2° 实测 12.24±9.90，0.25° 8.03±9.90）；
  - “PA 偏差 1.2σ”若无字段支撑，删除或补字段；
  - 端口统一为 8790（或同步 server 默认值）；
  - 注明 `mode=sample` 的真实用途。
- 不要改 `docs/01_理论设计编排.md` 的理论部分；只改与实现不符的 API/数字。

## 文件所有权 / 边界

**你可以改：**
- `app/server.py`（除 `run_analysis` 的 custom 分支、`build_dataset` 的 custom/fallback 逻辑）
- `app/web/app.js`、`app/web/index.html`、`app/web/style.css`
- `app/core/pipeline.py`（只改 quality 合并）
- `app/core/data.py` 的 `_cache_path` / `fetch_epoch` 缓存函数（不要改 `build_dataset` 的 custom/fallback 分支）
- `app/tests/` 新建
- `README.md`、`docs/02_实现与实测记录.md` 中与本工作相关的部分

**你不要改：**
- `app/core/motion.py` 的分类阈值/模型（只允许读）
- `app/core/data.py::build_dataset` 的自定义坐标/降级逻辑（T2 负责）
- `app/server.py::run_analysis` 的 custom 分支（T2 负责）
- `数据库工具/`、`视图工具/`、`项目主控/` 审计结论

> 如果 T2 正在并行修改 `app/core/data.py`，请只改缓存函数，避免和 `build_dataset` 冲突；交付时列出你改的函数名。

## 验收命令

```bash
PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
cd /Users/yiao/Documents/huellaX
$PY app/server.py --port 8790   # 若未启动

# 1) 5 目标不回归
for t in barnard epsind_b wise0855 rw_aur r_dra; do
  curl -s -X POST http://127.0.0.1:8790/api/analyze \
    -H 'Content-Type: application/json' \
    -d "{\"target_id\":\"$t\",\"mode\":\"auto\",\"fov\":0.25}" \
  | $PY -c 'import json,sys;d=json.load(sys.stdin);r=d["result"];print(d["target"]["id"],d["data"]["mode"],r["readiness"],r["class"],r.get("mu_mas_yr"))'
done

# 2) sample / 非法 FOV
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8790/api/analyze \
  -H 'Content-Type: application/json' -d '{"target_id":"barnard","mode":"sample","fov":0.25}'
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8790/api/analyze \
  -H 'Content-Type: application/json' -d '{"target_id":"barnard","mode":"auto","fov":-0.2}'

# 3) 单测
$PY -m unittest discover -s app/tests -v
```

**期望：**

- 5 目标结果与快照一致（Eps `4707±547 / L3 / L`；Barnard `L2 / S`；WISE `L0 / U`；RW/R Dra `V`，mode 不变）。
- sample 返回 200 或 422，不是 500。
- `fov=-0.2` 返回 400。
- 单测全过。
- `#modelsWrap` 不再 hidden；`#epochList` 有内容。

## 交付格式

按 `项目主控/提示词/README.md` §6 输出：变更清单、验收证据、未完成/风险、回滚方式。附 5 目标前后对比表。

## 绝对不要做

- 为了让 Barnard/WISE 变成 L 而放宽显著性、改阈值、改数据。
- 把 HYBRID 结果标成 REAL。
- 为了让 BIC 面板有内容而伪造模型。
- 修改 `数据库工具/` 或 `视图工具/`。
