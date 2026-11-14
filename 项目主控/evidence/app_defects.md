# MoveScope · 验证记录

快照：2026-11-14 23:07 CET。服务 :8790 已在运行；热缓存；未修改源码。审计期间 `app/core/motion.py`（23:03）、`app/web/app.js`（23:02）被并行编辑，以下结论在该快照复测。

## 1. 五目标实跑（mode=auto, fov=0.25）

| 目标 | 预期 | mode | n | readiness | class | μ ± σ (mas/yr) | detected | baseline | quality | 耗时 |
|---|---|---|---|---|---|---|---|---|---|---|
| barnard | L | real | 4 | L2 | **S** | 8033 ± 9899 | 4 | 204 d | SATURATED_SOURCE, LARGE_CENTROID_ERROR | 1.08 s |
| epsind_b | L+O | real | 8 | **L3** | **L** | **4707 ± 547** | 8 | 361 d | LARGE_CENTROID_ERROR | 2.17–2.34 s |
| wise0855 | L | real | 8 | L0 | **U** | — | 1 | 183 d（探测基线 0） | FEW_EPOCHS, SHORT_BASELINE, LARGE_CENTROID_ERROR | 2.30 s |
| rw_aur | V | hybrid | 8 | L2 | **V** | 49 ± 2116 | 8 | 205 d（模拟） | LARGE_CENTROID_ERROR | 2.39–2.41 s |
| r_dra | V | hybrid | 8 | L2 | **V** | 795 ± 2437 | 7 | 205 d（模拟） | LARGE_CENTROID_ERROR | 2.62 s |

- 5/5 HTTP 200；3/5 真实数据；**真实数据上正确运动检测 1/5**（仅 Eps Ind B）。
- Barnard 的位移 5.35″ 接近真值 5.8″，但质心误差 0.4 px，μ 不显著，判 S 是诚实做法。
- WISE 8 期里只有 1 期达 3σ；L0/U 是正确的不作为。
- Eps Ind B 与文档一致：μ=4706.66±547.27 mas/yr、PA=129.62°、位移 4.408±0.984″、L3、M2 44.7%/M3 32.8%/M4 22.6%、ΔBIC=142.3。

## 2. 自定义坐标

```bash
curl -X POST http://127.0.0.1:8790/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"ra":269.452077,"dec":4.693365,"fov":0.25}'
```

返回：

- `data.mode = synthetic`
- `data.n_epochs = 8`
- `data.provenance = ["synthetic"]`
- `note = "自定义坐标：使用模拟时序演示识别流程"`

**即使坐标精确等于 Barnard、数据库里有 4 期真实历元，仍然是 synthetic。**
`app/server.py::run_analysis` 的 custom 分支只调 `generate_epochs()`，不查 `epoch_catalog`、IRSA 或数据库。

补充：`GET /api/epochs?ra=&dec=&fov=` 返回 `{"epochs":[]}`（app 的 GET 端点不按坐标查，只按 `target_id` 查本地 JSON）。这进一步说明“任意坐标”没有数据通路。

## 3. 错误路径

| 请求 | 结果 |
|---|---|
| `{"target_id":"barnard","mode":"sample","fov":0.25}` | **500** `cannot convert float NaN to integer`（0.017 s） |
| `{"target_id":"barnard","mode":"auto","fov":-0.2}` | **500** `high - low < 0` |
| `{"target_id":"barnard","mode":"auto","fov":0}` | 静默使用 24 px（约 0.041°），与文档最小 0.02° 不符 |
| `{"target_id":"barnard","ra":10,"dec":10}` | 仍返回 Barnard；覆盖坐标被静默丢弃 |
| 模拟 IRSA 全失败 | 8 期 `provenance=synthetic`，但 `mode=hybrid`，note 写“基线 361 天不足以判定” |

**断网误报的复现**（只 monkeypatch `fetch_epoch`，不改文件）：

```text
wise0855 -> {'mode':'hybrid','note':'真实历元 8 期但基线仅 183 天，不足以判定运动规律；以下为验证时序（HYBRID）。'} | prov ['synthetic']
epsind_b -> {'mode':'hybrid','note':'真实历元 8 期但基线仅 361 天，不足以判定运动规律；以下为验证时序（HYBRID）。'} | prov ['synthetic']
barnard  -> {'mode':'hybrid','note':'真实历元 4 期但基线仅 204 天，不足以判定运动规律；以下为验证时序（HYBRID）。'} | prov ['synthetic']
```

`note` 依据的是**目录里的历元**，不是**成功取回的像素数**；“取数失败”被写成“基线不足”。

## 4. UI 缺陷

- `#modelsWrap`（BIC 模型面板）在 HTML 里始终带 `class="models hidden"`；`app.js` 只往 `#models` 里填内容，**从不移除父级的 hidden** → BIC 永远不可见。
- `#epochList` 从不填充 → 研究者模式的“历元列表”是空的。
- README/文档承诺的 `/api/cutout`、`/api/frame/{epoch_id}.png`、平移/缩放、SED、速度控制、悬停波长都不存在。
- 质量标记被覆盖：`motion.analyze_track()` 会 append `BLEND_SUSPECTED` / `BLEND_INDUCED_WANDER` / `IMPLAUSIBLE_PM`，但 `pipeline.run_pipeline()` 最后执行 `result["quality"] = quality_flags(track)`，整体替换。API/UI 永远看不到这三个标记。

## 5. 资源与缓存

- `RESULTS` 字典只增不减：每次分析保存所有 PNG，实测 1.85–4.75 MB/次；`results_cached` 从 1 涨到 17。公网/长时间演示会耗尽内存。
- `_cache_path` 只有 `release/obsid/detector/ra/dec/size`，**不含处理版本**；新 release 会复用旧像素。
- 缓存命中无 `try/except`；`np.savez` 非原子；坏文件会永久 500。
- 无 FOV 上限：3.5° 会分配约 2,049²×8 float64 ≈ 269 MB（在管线复制前）。
- 默认端口 8765，README 写 8790。

## 6. 文档数字偏差

| 文档 | 实测 | 判定 |
|---|---|---|
| 40/40 注入-回收，分类 100% | 40/40，100% | ✅（但为合成） |
| 中位 μ 回收比 0.98/1.07/1.14；PA 误差 13.8°/10.1°/1.9°；显著性 6.5/3.8/6.1σ | 复算 0.979/1.073/1.139；13.76°/10.15°/1.857°；6.545/3.840/6.070σ | ✅ |
| Eps Ind B μ=4.71±0.55″/yr，PA=129.6°，L3 | 4706.66±547.27 mas/yr，129.62°，L3 | ✅ |
| “6–34 颗匹配星，RMS 0.07–0.42 px” | `epsind_b.json`：10–21 颗，0.108–0.784 px | ❌ |
| Barnard 0.2° → μ=8.0±9.9″/yr | 0.2° 实测 12.24±9.90；8.03 是 0.25° 口径 | ❌ |
| “PA 偏差 1.2σ” | 无 PA 误差字段，不可复算 | ⚠️ |
| 饱和峰值 755–1766 MJy/sr | 755–1782 | ⚠️ 近似 |
| WISE L0/U/1 期探测 | 一致 | ✅ |

## 7. 真正强的部分

- **Eps Ind B 真实数据结果**：8 期真实 SPHEREx 切片、361 天、L3、参考星仿射定标、μ 与 SIMBAD 差 0.5%。这是全项目最有说服力的资产。
- **诚实的失败模式**：WISE 判 U、Barnard 判 S 并标 `SATURATED_SOURCE`，没有为了“好看”硬报运动。
- **可解释管线**：BIC 模型族、11 维指纹、公众话术、可证伪条件；40/40 合成回收 + 新的 A/D 变光控制。
- **演示速度**：热缓存 1.1–2.6 s；无构建三栏 UI；四种显示模式。

## 8. 离线实测（正面）

阻断 `urllib.request.urlopen` 后跑完整 `build_dataset + run_pipeline`：

| 目标 | mode | n | readiness | class | μ |
|---|---|---|---|---|---|
| barnard | real | 4 | L2 | S | 8032.88 |
| epsind_b | real | 8 | L3 | L | 4706.66 |
| wise0855 | real | 8 | L0 | U | — |
| rw_aur | hybrid | 8 | L2 | V | 49.33 |
| r_dra | hybrid | 8 | L2 | V | 794.94 |

结果与在线完全一致，耗时 0.3–0.6 s/目标。缓存现有 55 个 NPZ / 15 个 pointing 区域。
**结论：5 目标离线演示已可用；缺的是清单/sha256/可移植快照，以及任意坐标的冻结。**
但若某期缓存缺失，`build_dataset` 会把“取数失败”误报为“基线不足”（见 §3）。

## 9. 结论

**产品形态成立，验收路径有硬洞。**
- 保留并加强：Eps 真实结果、BIC/指纹/话术、四种显示。
- 必须修：R1 自定义坐标、sample/FOV 500、BIC/历元面板、quality 累加、断网误报、缓存/内存。
- 不应做：为了“5 个都动”而调参/放宽显著性；应如实讲数据边界。
