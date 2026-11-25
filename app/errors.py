from flask import jsonify


MESSAGES = {
    "EMPTY_FILE": "文件为空",
    "BLANK_FILE": "文件只有空白",
    "HEADER_ONLY": "文件只有表头",
    "HEADER_MISMATCH": "表头与本项目字段不一致",
    "UNCLOSED_QUOTE": "引号未闭合，文件已拒收",
    "TOO_MANY_ROWS": "数据行超过上限",
    "FILE_TOO_LARGE": "文件超过大小上限",
    "KEY_MISSING": "主键为空",
    "CONFLICT_DUPLICATE": "同一键存在冲突内容",
    "SAMPLE_UNKNOWN": "样例不存在",
    "STAGE_UNKNOWN": "阶段不存在",
    "RUN_NOT_FOUND": "批次不存在",
    "STALE_REVIEW": "批次已被更新，请刷新后复核",
    "GATE_BLOCKED": "存在阻断项，不能签发",
    "NOT_APPROVED": "批次尚未签发",
    "BAD_REQUEST": "请求内容不完整",
    "RUN_ID_REQUIRED": "缺少批次编号",
    "HANDOVER_ID_REQUIRED": "缺少交接分账编号",
    "HANDOVER_DATE_REQUIRED": "至少需要一份交接记录日期",
    "CONTAINER_REQUIRED": "请选择交接箱子",
    "CONTAINER_NOT_FOUND": "批次中找不到可复算箱子",
    "HANDOVER_NOT_FOUND": "交接分账不存在",
    "DAY_CHARGE_CONFLICT": "同一日费已按不同金额签发",
    "DAY_OWNER_CONFLICT": "同一日费已签发给另一货主",
}


class DomainIssue(Exception):
    def __init__(self, code: str, status: int = 400, detail: str | None = None):
        self.code = code
        self.status = status
        self.detail = detail
        super().__init__(code)


def error_response(exc: DomainIssue):
    return jsonify(error=exc.detail or MESSAGES.get(exc.code, "请求无法处理"), code=exc.code), exc.status
