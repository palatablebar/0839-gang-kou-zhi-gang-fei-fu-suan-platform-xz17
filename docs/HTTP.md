# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `POST /api/runs/{id}/handovers/preview`：按交接记录预览新旧货主、争议日和待定费用。
- `POST /api/runs/{id}/handovers`：签发交接分账；相同请求重试返回同一单据。
- `POST /api/handovers/{handover_id}/issue`：重试签发已有交接分账，日费按天幂等。
- `GET /api/runs/{id}/handovers`：列出交接分账。
- `GET /api/handovers/{handover_id}/reconciliation`：取得交接对账恒等式结果。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。
