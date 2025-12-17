from __future__ import annotations


class DocBusyError(RuntimeError):
    def __init__(self, doc_id: str, job_id: int | None = None) -> None:
        super().__init__(f"Document {doc_id} occupé.")
        self.doc_id = doc_id
        self.job_id = job_id
