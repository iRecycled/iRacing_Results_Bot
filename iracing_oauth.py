"""
iRacing OAuth 2.1 Password-Limited authentication helper
Per iRacing spec: https://oauth.iracing.com/oauth2/book/token_endpoint.html#client-secret-and-user-password-masking
"""
import hashlib
import base64

def mask_secret(secret, identifier):
    """
    Mask a secret (client_secret or password) using the identifier.

    Per iRacing spec:
    1. Normalize the identifier: trim whitespace and convert to lowercase
    2. Concatenate: secret + normalized_identifier (no separator)
    3. Hash: Apply SHA-256
    4. Encode: Base64 encode the hash

    Args:
        secret: The client_secret or user password
        identifier: The client_id (for client_secret) or username (for password)

    Returns:
        Base64-encoded SHA-256 hash string
    """
    normalized_id = identifier.strip().lower()
    combined = secret + normalized_id
    hash_bytes = hashlib.sha256(combined.encode('utf-8')).digest()
    masked = base64.b64encode(hash_bytes).decode('utf-8')
    return masked
