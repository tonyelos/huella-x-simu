# 官方 5 个实例基准报告

> 只读基准：不写 DB、不改 targets.json；用于机制回归，不是“标记/确认”。

| 目标 | 状态 | expected | actual | n | readiness | identity | RUWE | 说明 |
|---|---|---|---|---:|---|---|---|---|
| Barnard's Star | **PASS** | L | L | 4 | L2 | known_star | 4472832130942575872=1.08485 | class L vs L; identity known_star vs known_star; n_epochs=4 readiness=L2; catalog_prior=catalog_prior; quality=SATURATED_SOURCE,LARGE_CENTROID_ERROR,CATALOG_PRIOR_MOTION,MEASUREMENT_NOT_SIGNIFICANT |
| Epsilon Indi B | **PASS** | L | L | 8 | L3 | ambiguous | 6412596012146801152=4.295541 | class L vs L; identity ambiguous vs ambiguous; n_epochs=8 readiness=L3; catalog_prior=none; quality=LARGE_CENTROID_ERROR |
| WISE 0855 | **DATA_LIMITED** | L | U | 8 | L0 | insufficient_data | — | class U vs L; identity insufficient_data vs insufficient_data; n_epochs=8 readiness=L0; catalog_prior=none; quality=FEW_EPOCHS,SHORT_BASELINE,LARGE_CENTROID_ERROR |
| RW Aurigae | **PASS** | V | V | 8 | L2 | ambiguous | 156430817820015232=16.422132, 156430822114424576=1.500373 | class V vs V; identity ambiguous vs ambiguous; n_epochs=8 readiness=L2; catalog_prior=none; quality=LARGE_CENTROID_ERROR |
| R Draconis | **PASS** | V | V | 8 | L2 | known_star | 1648328016354496384=1.297982 | class V vs V; identity known_star vs known_star; n_epochs=8 readiness=L2; catalog_prior=none; quality=LARGE_CENTROID_ERROR |

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
    "state": "PASS",
    "expected_class": "L",
    "actual_class": "L",
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
    "catalog_motion": {
      "pm_mas_yr": 10393.348717415127,
      "pm_err_mas_yr": 0.04810873101631345,
      "basis": "catalog_prior",
      "consistent_with_measurement": true,
      "measurement_significance": 1.2365643634230588,
      "catalog_id": "4472832130942575872"
    },
    "quality": [
      "SATURATED_SOURCE",
      "LARGE_CENTROID_ERROR",
      "CATALOG_PRIOR_MOTION",
      "MEASUREMENT_NOT_SIGNIFICANT"
    ],
    "detail": "class L vs L; identity known_star vs known_star; n_epochs=4 readiness=L2; catalog_prior=catalog_prior; quality=SATURATED_SOURCE,LARGE_CENTROID_ERROR,CATALOG_PRIOR_MOTION,MEASUREMENT_NOT_SIGNIFICANT",
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
    "catalog_motion": {
      "pm_mas_yr": 4684.16455398763,
      "pm_err_mas_yr": 0.8680687645572787,
      "basis": "none",
      "consistent_with_measurement": true,
      "measurement_significance": 8.07413412796928,
      "catalog_id": "6412596012146801152"
    },
    "quality": [
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class L vs L; identity ambiguous vs ambiguous; n_epochs=8 readiness=L3; catalog_prior=none; quality=LARGE_CENTROID_ERROR",
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
    "catalog_motion": {
      "pm_mas_yr": null,
      "pm_err_mas_yr": null,
      "basis": "none",
      "consistent_with_measurement": null,
      "measurement_significance": 0.0,
      "catalog_id": null
    },
    "quality": [
      "FEW_EPOCHS",
      "SHORT_BASELINE",
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class U vs L; identity insufficient_data vs insufficient_data; n_epochs=8 readiness=L0; catalog_prior=none; quality=FEW_EPOCHS,SHORT_BASELINE,LARGE_CENTROID_ERROR",
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
    "catalog_motion": {
      "pm_mas_yr": 25.06236904703943,
      "pm_err_mas_yr": 0.41616559204239845,
      "basis": "none",
      "consistent_with_measurement": true,
      "measurement_significance": 0.2433575355078826,
      "catalog_id": "156430817820015232"
    },
    "quality": [
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class V vs V; identity ambiguous vs ambiguous; n_epochs=8 readiness=L2; catalog_prior=none; quality=LARGE_CENTROID_ERROR",
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
    "catalog_motion": {
      "pm_mas_yr": 21.539102425124405,
      "pm_err_mas_yr": 0.1248106565963019,
      "basis": "none",
      "consistent_with_measurement": true,
      "measurement_significance": 0.156726404993449,
      "catalog_id": "1648328016354496384"
    },
    "quality": [
      "LARGE_CENTROID_ERROR"
    ],
    "detail": "class V vs V; identity known_star vs known_star; n_epochs=8 readiness=L2; catalog_prior=none; quality=LARGE_CENTROID_ERROR",
    "error": null
  }
]
```
