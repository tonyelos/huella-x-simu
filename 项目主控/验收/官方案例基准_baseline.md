# 官方 5 个实例基准报告

> 只读基准：不写 DB、不改 targets.json；用于机制回归，不是“标记/确认”。

| 目标 | 状态 | expected | actual | n | readiness | identity | RUWE | 说明 |
|---|---|---|---|---:|---|---|---|---|
| Barnard's Star | **FAIL** | L | S | 4 | L2 | known_star | 4472832130942575872=1.08485 | class S vs L; identity known_star vs known_star; n_epochs=4 readiness=L2; quality=SATURATED_SOURCE,LARGE_CENTROID_ERROR |
| Epsilon Indi B | **PASS** | L | L | 8 | L3 | ambiguous | 6412596012146801152=4.295541 | class L vs L; identity ambiguous vs ambiguous; n_epochs=8 readiness=L3; quality=LARGE_CENTROID_ERROR |
| WISE 0855 | **DATA_LIMITED** | L | U | 8 | L0 | insufficient_data | — | class U vs L; identity insufficient_data vs insufficient_data; n_epochs=8 readiness=L0; quality=FEW_EPOCHS,SHORT_BASELINE,LARGE_CENTROID_ERROR |
| RW Aurigae | **PASS** | V | V | 8 | L2 | ambiguous | 156430817820015232=16.422132, 156430822114424576=1.500373 | class V vs V; identity ambiguous vs ambiguous; n_epochs=8 readiness=L2; quality=LARGE_CENTROID_ERROR |
| R Draconis | **PASS** | V | V | 8 | L2 | known_star | 1648328016354496384=1.297982 | class V vs V; identity known_star vs known_star; n_epochs=8 readiness=L2; quality=LARGE_CENTROID_ERROR |

## 状态定义

- **PASS**：与官方预期一致，且没有超出数据能力；
- **DATA_LIMITED**：数据不足，但明确 U/insufficient，不伪造；
- **FAIL**：与预期冲突且不是数据限制。

## 原始 JSON

```json
[
  {
    "id": "barnard",
    "name": "Barnard's Star",
    "state": "FAIL",
    "expected_class": "L",
    "actual_class": "S",
    "expected_identity": "known_star",
    "actual_identity": "known_star",
    "n_epochs": 4,
    "readiness": "L2",
    "ruwe": [
      {
        "source_id": "4472832130942575872",
        "ruwe": 1.08485
      }
    ],
    "catalog_motion": null,
    "quality": [
      "SATURATED_SOURCE",
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class S vs L; identity known_star vs known_star; n_epochs=4 readiness=L2; quality=SATURATED_SOURCE,LARGE_CENTROID_ERROR",
    "error": null
  },
  {
    "id": "epsind_b",
    "name": "Epsilon Indi B",
    "state": "PASS",
    "expected_class": "L",
    "actual_class": "L",
    "expected_identity": "ambiguous",
    "actual_identity": "ambiguous",
    "n_epochs": 8,
    "readiness": "L3",
    "ruwe": [
      {
        "source_id": "6412596012146801152",
        "ruwe": 4.295541
      }
    ],
    "catalog_motion": null,
    "quality": [
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class L vs L; identity ambiguous vs ambiguous; n_epochs=8 readiness=L3; quality=LARGE_CENTROID_ERROR",
    "error": null
  },
  {
    "id": "wise0855",
    "name": "WISE 0855",
    "state": "DATA_LIMITED",
    "expected_class": "L",
    "actual_class": "U",
    "expected_identity": "insufficient_data",
    "actual_identity": "insufficient_data",
    "n_epochs": 8,
    "readiness": "L0",
    "ruwe": [],
    "catalog_motion": null,
    "quality": [
      "FEW_EPOCHS",
      "SHORT_BASELINE",
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class U vs L; identity insufficient_data vs insufficient_data; n_epochs=8 readiness=L0; quality=FEW_EPOCHS,SHORT_BASELINE,LARGE_CENTROID_ERROR",
    "error": null
  },
  {
    "id": "rw_aur",
    "name": "RW Aurigae",
    "state": "PASS",
    "expected_class": "V",
    "actual_class": "V",
    "expected_identity": "ambiguous",
    "actual_identity": "ambiguous",
    "n_epochs": 8,
    "readiness": "L2",
    "ruwe": [
      {
        "source_id": "156430817820015232",
        "ruwe": 16.422132
      },
      {
        "source_id": "156430822114424576",
        "ruwe": 1.500373
      }
    ],
    "catalog_motion": null,
    "quality": [
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class V vs V; identity ambiguous vs ambiguous; n_epochs=8 readiness=L2; quality=LARGE_CENTROID_ERROR",
    "error": null
  },
  {
    "id": "r_dra",
    "name": "R Draconis",
    "state": "PASS",
    "expected_class": "V",
    "actual_class": "V",
    "expected_identity": "known_star",
    "actual_identity": "known_star",
    "n_epochs": 8,
    "readiness": "L2",
    "ruwe": [
      {
        "source_id": "1648328016354496384",
        "ruwe": 1.297982
      }
    ],
    "catalog_motion": null,
    "quality": [
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class V vs V; identity known_star vs known_star; n_epochs=8 readiness=L2; quality=LARGE_CENTROID_ERROR",
    "error": null
  }
]
```
