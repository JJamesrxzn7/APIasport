import argparse
import getpass
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_API_URL = "https://apex-sports-api-ns1y.onrender.com/api/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the first Apex Sports superadministrator using a one-time bootstrap secret."
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"API base URL (default: {DEFAULT_API_URL})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_url = args.api_url.rstrip("/")
    parsed_url = urlsplit(api_url)
    if parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1"}:
        print("The API URL must use HTTPS.", file=sys.stderr)
        return 2
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        print("Provide a valid API base URL.", file=sys.stderr)
        return 2

    secret = getpass.getpass("One-time bootstrap secret: ")
    full_name = input("Administrator full name: ").strip()
    email = input("Administrator email: ").strip().lower()
    password = getpass.getpass("New administrator password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm administrator password: ")

    if len(secret) < 32:
        print("The bootstrap secret must contain at least 32 characters.", file=sys.stderr)
        return 2
    if password != confirmation:
        print("The passwords do not match.", file=sys.stderr)
        return 2
    if len(password) < 12:
        print("The password must contain at least 12 characters.", file=sys.stderr)
        return 2

    payload = json.dumps(
        {"full_name": full_name, "email": email, "password": password}
    ).encode("utf-8")
    request = Request(
        f"{api_url}/auth/admin/bootstrap",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Admin-Bootstrap-Secret": secret,
        },
        method="POST",
    )
    del secret, password, confirmation, payload

    try:
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get("detail", "Request rejected.")
        except (json.JSONDecodeError, AttributeError):
            detail = "Request rejected."
        print(f"Bootstrap failed (HTTP {error.code}): {detail}", file=sys.stderr)
        return 1
    except URLError as error:
        print(f"Could not reach the API: {error.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print("The API request timed out. Check the service and try again.", file=sys.stderr)
        return 1

    user = result.get("user", {})
    print(f"Created {user.get('name', full_name)} <{user.get('email', email)}>.")
    print("Role: superadministrador.")
    print("The bootstrap is now closed. Remove ADMIN_BOOTSTRAP_SECRET from Render.")
    print("Sign in to the administration panel with the new account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
