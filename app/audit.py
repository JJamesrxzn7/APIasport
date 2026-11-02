import json
import secrets
import string

from sqlalchemy import text
from sqlalchemy.orm import Session


def write_audit(
    db: Session,
    user_id: str,
    table_name: str,
    action: str,
    new_value: dict | None = None,
    client_ip: str | None = None,
) -> None:
    audit_id = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(15))
    db.execute(
        text(
            "INSERT INTO log_auditoria "
            "(id_log, id_usuario, log_fechahora, log_tablaafectada, log_accion, "
            "log_ip, log_valornuevo) "
            "VALUES (:id, :user, CURRENT_TIMESTAMP, :table_name, :action, :ip, :value)"
        ),
        {
            "id": audit_id,
            "user": user_id,
            "table_name": table_name,
            "action": action,
            "ip": client_ip,
            "value": json.dumps(new_value, ensure_ascii=False, default=str) if new_value is not None else None,
        },
    )
