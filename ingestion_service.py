"""Deterministic ingestion with SHA-256 idempotency and DB persistence."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.orm import Session

from models import AuditLog, Company, Document, DocumentChunk
from parser_layer import parse_document
from workflow_states import WorkflowState

ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".txt", ".md", ".doc", ".ppt"})
DEFAULT_MAX_BYTES = int(os.environ.get("FILE_SIZE_LIMIT_MB", "50")) * 1024 * 1024


@dataclass
class IngestionResult:
    correlation_id: str
    company_id: str
    document_id: str
    status: str
    duplicate_detected: bool
    message: str


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    return base or "upload.bin"


def validate_upload(filename: str, data: bytes) -> None:
    if not data:
        raise ValueError("Empty file payload")
    if len(data) > DEFAULT_MAX_BYTES:
        raise ValueError(f"File exceeds size limit of {DEFAULT_MAX_BYTES} bytes")
    ext = os.path.splitext(_safe_filename(filename))[1].lower()
    allowed = os.environ.get("ALLOWED_FILE_TYPES", "")
    if allowed:
        allowed_set = {e if e.startswith(".") else f".{e}" for e in allowed.split(",")}
        if ext not in allowed_set:
            raise ValueError(f"File type '{ext}' is not allowed")
    elif ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type '{ext}' is not allowed")


def _write_audit(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    payload: dict | None = None,
) -> None:
    import json

    session.add(
        AuditLog(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload_json=json.dumps(payload) if payload else None,
        )
    )


def ingest_document(
    session: Session,
    *,
    file_bytes: bytes,
    filename: str,
    company_name: str,
    deal_id: str | None = None,
    domain: str | None = None,
    correlation_id: str | None = None,
    storage_dir: str | None = None,
) -> IngestionResult:
    """
    Ingest bytes with SHA-256 dedup. Returns existing document if checksum matches.
    """
    validate_upload(filename, file_bytes)
    correlation_id = correlation_id or str(uuid.uuid4())
    checksum = compute_sha256(file_bytes)
    safe_name = _safe_filename(filename)

    existing = session.query(Document).filter_by(checksum_sha256=checksum).first()
    if existing:
        company = session.get(Company, existing.company_id)
        _write_audit(
            session,
            "duplicate_detected",
            "document",
            existing.id,
            correlation_id,
            {"checksum": checksum},
        )
        return IngestionResult(
            correlation_id=correlation_id,
            company_id=existing.company_id,
            document_id=existing.id,
            status=existing.status,
            duplicate_detected=True,
            message="Duplicate document detected; returning existing record.",
        )

    company = Company(
        name=company_name,
        deal_id=deal_id,
        domain=domain,
        status=WorkflowState.PENDING.value,
        correlation_id=correlation_id,
    )
    session.add(company)
    session.flush()

    storage_path = None
    if storage_dir:
        os.makedirs(storage_dir, exist_ok=True)
        storage_path = os.path.join(storage_dir, f"{checksum}_{safe_name}")
        with open(storage_path, "wb") as f:
            f.write(file_bytes)

    document = Document(
        company_id=company.id,
        filename=safe_name,
        checksum_sha256=checksum,
        file_size_bytes=len(file_bytes),
        storage_path=storage_path,
        status=WorkflowState.PENDING.value,
        correlation_id=correlation_id,
    )
    session.add(document)
    session.flush()

    parse_source: str | bytes = storage_path or file_bytes
    chunks = parse_document(parse_source, filename=safe_name)
    for chunk in chunks:
        session.add(
            DocumentChunk(
                document_id=document.id,
                page_index=chunk.page_index,
                raw_text=chunk.raw_text,
                extraction_method=chunk.extraction_method,
                confidence_score=chunk.confidence_score,
            )
        )

    if chunks:
        document.status = WorkflowState.COMPLETED.value
        company.status = WorkflowState.COMPLETED.value
    else:
        document.status = WorkflowState.FAILED.value
        company.status = WorkflowState.FAILED.value

    _write_audit(
        session,
        "upload_received",
        "document",
        document.id,
        correlation_id,
        {"checksum": checksum, "filename": safe_name, "chunk_count": len(chunks)},
    )

    return IngestionResult(
        correlation_id=correlation_id,
        company_id=company.id,
        document_id=document.id,
        status=document.status,
        duplicate_detected=False,
        message="Document ingested successfully." if chunks else "Document stored but parsing produced no chunks.",
    )


def ingest_from_filelike(
    session: Session,
    file_obj: BinaryIO,
    filename: str,
    company_name: str,
    **kwargs,
) -> IngestionResult:
    data = file_obj.read()
    return ingest_document(session, file_bytes=data, filename=filename, company_name=company_name, **kwargs)
