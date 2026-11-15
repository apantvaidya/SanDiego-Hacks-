"""FastAPI app and Sendblue webhook. Owned by the messaging issue."""
from fastapi import FastAPI, Request

app = FastAPI(title="Care Navigator")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/webhooks/sendblue")
async def sendblue_webhook(request: Request) -> dict:
    """Parse inbound message, call conversation.router.handle, send each reply.

    Must return quickly: do the work in a background task so Sendblue does not retry.
    Dedupe on message_id.
    """
    raise NotImplementedError
