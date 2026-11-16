"""Create .env once with a fresh local encryption key; never print secret values."""
from pathlib import Path
import os

from cryptography.fernet import Fernet

root = Path(__file__).resolve().parents[1]
destination = root / ".env"
if destination.exists():
    print(".env already exists; no settings changed.")
else:
    template = (root / ".env.example").read_text(encoding="utf-8")
    template = template.replace("TOKEN_ENCRYPTION_KEY=\n", "TOKEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode() + "\n")
    with destination.open("x", encoding="utf-8") as output:
        output.write(template)
    if os.name != "nt":
        destination.chmod(0o600)
    print("Created .env with an encryption key. Add GROQ_API_KEY and your Google OAuth credentials.")
