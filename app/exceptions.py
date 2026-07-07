from __future__ import annotations


class MFARequiredError(Exception):
    def __init__(self, email: str):
        self.email = email
        super().__init__(f"MFA/2FA code required for {email}")
