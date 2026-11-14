# SPHEREx FITS 头缓存（生成物）

- 由 `数据库工具/tools/harvest_headers.py` 通过 S3 Range 只读 64 KB 生成；
- 当前 74 份，对应 DB `target_epochs` 的 74 条候选历元；
- manifest：`../header_manifest.json`（key / sha256 / bytes / status）；
- 这是网络缓存，**可以删除**；重采：
  ```bash
  cd ../../数据库工具
  PY=/Users/yiao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3
  $PY tools/harvest_headers.py --source epochs --resume
  ```
- 不要手工伪造 `.hdr`；`ingest_headers` 只认 FITS 卡片，假头会进不了库。
