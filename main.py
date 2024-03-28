from starlette.requests import Request
from starlette.responses import Response
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket
from starlette.routing import Route, WebSocketRoute
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_until_first_complete, run_in_threadpool
from starlette.websockets import WebSocketDisconnect
import orjson as json
import anyio
from anyio import TASK_STATUS_IGNORED
from anyio import to_thread
from anyio import create_memory_object_stream
from anyio.streams.memory import MemoryObjectReceiveStream
from pydantic import BaseModel
import uvicorn
import click
import structlog
import contextlib
from structlog.stdlib import BoundLogger
from typing import Optional, TypeVar, Generic, cast, Any
from app.template import evaluate_template
from enum import Enum, auto


# https://gist.github.com/nymous/f138c7f06062b7c43c060bf03759c29e
# https://github.com/hynek/structlog/issues/360

def slog() -> BoundLogger:
    return structlog.get_logger()


async def handler(request: Request) -> Response:
    host = request.client.host if request.client else None
    log = slog().bind(host=host, path=request.url.path, method=request.method)
    method = request.method
    match method:
        case "GET":
            return JSONResponse({"payload": "hello"})
        case "POST":
            payload = await request.json()
            try:
                result = await evaluate_template(payload)
                return JSONResponse(result)
            except Exception as e:
                return Response(str(e), status_code=400)
        case _:
            return JSONResponse({"payload": "method not allowed"},
                                status_code=405)

@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    log = slog()
    log.info("lifespan starts")
    async with anyio.create_task_group() as tg:
        yield
        tg.cancel_scope.cancel()
    log.info("lifespan end")

@click.command()
@click.option("--port", default=8000, help="Port number")
@click.option("--host", default="0.0.0.0", help="Host")
def main(port: int, host: str):
    from importlib.metadata import version
    anyio_version_str = version("anyio").split(".")
    anyio_version = tuple([int(x) for x in anyio_version_str])
    assert anyio_version[0] == 4 and anyio_version[
        1] >= 3, "anyio version must be >= 4.3"
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]
    app = Starlette(debug=True,
                    routes=[
                        Route("/eval", handler),
                    ],
                    middleware=middleware)  # type: ignore
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter
