"""Errors in OpenAI's format: {"error": {"message", "type", "param", "code"}}."""

from collections.abc import Mapping, Sequence

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class OpenAIError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        error_type: str = "invalid_request_error",
        code: str | None = None,
        param: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.code = code
        self.param = param
        self.headers = dict(headers or {})

    def to_response(self) -> JSONResponse:
        error = {"message": self.message, "type": self.error_type, "param": self.param, "code": self.code}
        return JSONResponse(status_code=self.status_code, content={"error": error}, headers=self.headers)


def install_error_handlers(app: FastAPI) -> None:
    """Make every error the gateway returns, including FastAPI's own, use OpenAI's format."""
    app.exception_handler(OpenAIError)(_handle_openai_error)
    app.exception_handler(RequestValidationError)(_handle_validation_error)
    app.exception_handler(StarletteHTTPException)(_handle_http_error)
    app.exception_handler(Exception)(_handle_unexpected_error)


async def _handle_openai_error(request: Request, exc: OpenAIError) -> JSONResponse:
    return exc.to_response()


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    if first["type"] == "json_invalid":
        return OpenAIError(400, "We could not parse the JSON body of your request.").to_response()
    param = _param(first["loc"])
    message = f"Invalid value for '{param}': {first['msg']}" if param else first["msg"]
    return OpenAIError(400, message, param=param).to_response()


async def _handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return OpenAIError(exc.status_code, str(exc.detail), headers=exc.headers).to_response()


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Starlette re-raises the exception after sending this response, so the server still
    # logs the traceback; the client gets a parseable error without internal details.
    return OpenAIError(500, "The gateway hit an unexpected error.", error_type="server_error").to_response()


def _param(location: Sequence[str | int]) -> str | None:
    """("body", "messages", 0, "role") -> "messages[0].role", the way OpenAI names parameters."""
    param = ""
    for part in location[1:] if location[:1] == ("body",) else location:
        if isinstance(part, int):
            param += f"[{part}]"
        else:
            param += f".{part}" if param else part
    return param or None
