"""
webhook_verifier.py
───────────────────
Verifies that incoming webhook requests genuinely originate from GitHub
Actions by checking the HMAC-SHA256 signature attached to every request.
"""

import hmac
import hashlib


class WebhookVerifier:
    """
    Validates the X-Hub-Signature-256 header on incoming webhook requests.

    GitHub Actions signs the request body with the shared secret using
    HMAC-SHA256 and sends the result as:
        X-Hub-Signature-256: sha256=<hex-digest>

    This class recomputes the expected signature and compares it in
    constant time to prevent timing attacks.
    """

    def __init__(self, secret: str) -> None:
        """
        Parameters
        ----------
        secret : str
            The shared webhook secret stored in both GitHub Secrets and
            the server's environment (DOC_SERVER_WEBHOOK_SECRET).
        """
        if not secret:
            raise ValueError("Webhook secret must not be empty.")
        self._secret = secret.encode()

    def verify(self, body: bytes, signature_header: str) -> bool:
        """
        Return True if the request body matches the supplied signature.

        Parameters
        ----------
        body : bytes
            The raw request body exactly as received (before any parsing).
        signature_header : str
            The value of the X-Hub-Signature-256 header, e.g.
            "sha256=abc123...".

        Returns
        -------
        bool
            True when the signature is valid, False otherwise.
        """
        if not signature_header.startswith("sha256="):
            return False

        expected = "sha256=" + hmac.new(
            self._secret, body, hashlib.sha256
        ).hexdigest()

        # compare_digest runs in constant time to prevent timing attacks
        return hmac.compare_digest(expected, signature_header)
