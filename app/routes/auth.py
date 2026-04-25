"""Admin authentication dependency — HTTP Basic Auth."""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

security = HTTPBasic()


def require_admin(creds: HTTPBasicCredentials = Depends(security)):
    """Require valid admin credentials. Skipped if ADMIN_PASSWORD is empty."""
    if not settings.admin_password:
        return True  # no password set — auth disabled (gradual rollout)

    correct_password = secrets.compare_digest(
        creds.password, settings.admin_password
    )
    correct_username = secrets.compare_digest(
        creds.username, "admin"
    )

    if not correct_password or not correct_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True
