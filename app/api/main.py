"""FastAPI application factory.

Boundary rule: bad input produces clean 4xx responses — never an unhandled
500. RequestValidationError and domain errors are shaped explicitly.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import costs, images, jobs, posts, suggestions

log = logging.getLogger("api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Image Understanding & Content Matching Engine",
        version="1.0.0",
        description=(
            "Understands an image library, ranks images per blog post, and refuses "
            "bad pairings with explanations (mismatch guard)."
        ),
    )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # exc.errors() can embed raw request bytes in 'input' — not JSON-safe,
        # and echoing raw bodies back is poor hygiene. Return a sanitized shape.
        errors = [
            {"loc": [str(part) for part in e.get("loc", ())], "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": "validation error", "errors": errors})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.include_router(posts.router)
    app.include_router(images.router)
    app.include_router(jobs.router)
    app.include_router(suggestions.router)
    app.include_router(costs.router)
    return app


app = create_app()
