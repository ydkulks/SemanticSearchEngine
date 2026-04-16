from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.dependencies import get_db
from app.api.v1.schemas import IngestRequest, IngestResponse

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
def ingest_document(
    request: IngestRequest,
    db: Session = Depends(get_db),
):
    doc_id = None
    try:
        result = db.execute(
            text("""
                INSERT INTO documents (content, title, metadata)
                OUTPUT INSERTED.id
                VALUES (:content, :title, :metadata)
            """),
            {
                "content": request.content,
                "title": request.title,
                "metadata": __import__("json").dumps(request.metadata)
                if request.metadata
                else None,
            },
        )
        doc_id = result.scalar()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to ingest document: {str(e)}")

    return IngestResponse(id=doc_id, message="Document ingested successfully")
