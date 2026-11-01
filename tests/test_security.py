from app.security import hash_password, verify_password


def test_argon2_password_hash_verification() -> None:
    encoded = hash_password("this is a strong test password")
    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, "this is a strong test password")
    assert not verify_password(encoded, "incorrect password")
    assert not verify_password("not-a-hash", "incorrect password")
