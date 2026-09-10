import hmac

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from litlens.config import settings
from litlens.db.models import Library, SessionLocal

bearer = HTTPBearer(auto_error=False)


def owner(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials:
        for token, user in settings.api_tokens.items():
            if hmac.compare_digest(credentials.credentials, token):
                return user
    raise HTTPException(401, "Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})


def db():
    with SessionLocal() as session:
        yield session


def library_or_404(session, library_id, owner_id, lock=False):
    statement = select(Library).where(Library.id == library_id, Library.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    library = session.scalar(statement)
    if not library:
        raise HTTPException(404, "Library not found")
    return library
