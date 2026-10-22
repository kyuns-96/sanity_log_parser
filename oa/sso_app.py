#!/usr/bin/env python3
"""Company SSO composition root; scoped RBAC requires explicit admin configuration."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from oa.integration.corporate_sso import CorporateIdentityProvider, CorporateUserDirectory
from oa.dashboard_api import create_app

# Explicit mode: never use a configured legacy token if the company adapter fails.
# Company login/callback/logout: pass auth_router=your_APIRouter with relative
# routes /login, /callback, /logout. create_app mounts these under /auth before UI.
app = create_app(auth_mode="sso", identity_provider=CorporateIdentityProvider(),
                 user_directory=CorporateUserDirectory(), allowed_hosts=os.environ.get("OA_ALLOWED_HOSTS"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False, proxy_headers=False, log_level="critical")
