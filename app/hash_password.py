"""Generate an Argon2id hash for an administrator provisioned out of band."""

import getpass

from app.security import hash_password


def main() -> None:
    password = getpass.getpass("New account password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
