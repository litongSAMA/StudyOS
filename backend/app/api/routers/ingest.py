"""入库辅助路由：格式目录（`GET /api/ingest/formats`）。

前端的上传 `accept`、可上传格式清单与「支持哪些格式」提示全部由这个接口派生，
因此后端新增一种格式时，两个前端不需要各改一遍字符串常量。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.infra.ingest import ocr
from app.infra.ingest.formats import catalog
from app.infra.ingest.files import PDF_OCR_TEXT_MIN
from app.api.schemas import IngestFormatCatalog

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

#: 单文件上传上限（与 materials 路由保持一致，前端据此提示）
MAX_UPLOAD_MB = 512


@router.get("/formats", response_model=IngestFormatCatalog)
def list_formats() -> IngestFormatCatalog:
    """支持的文件格式目录：按分组列出扩展名、中文名、是否支持抽取、是否走 OCR。"""
    data = catalog(ocr_available=ocr.ocr_available())
    data["max_upload_mb"] = MAX_UPLOAD_MB
    data["pdf_ocr_text_min"] = PDF_OCR_TEXT_MIN
    return IngestFormatCatalog(**data)
