import logging
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from web.models import SystemConfig

log = logging.getLogger(__name__)

_SCHEMA_DRIFT_MARKER = "_schema_drift_fallback"
_SYSTEM_CONFIG_TABLE = "system_config"


def _is_populated(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _config_rank(source: object) -> tuple[int, datetime, str]:
    populated = 0
    for column in SystemConfig.__table__.columns:
        if column.name == "id":
            continue
        if _is_populated(getattr(source, column.name, None)):
            populated += 1

    updated_at = getattr(source, "updated_at", None)
    if not isinstance(updated_at, datetime):
        updated_at = datetime.min.replace(tzinfo=timezone.utc)
    elif updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    identifier = str(getattr(source, "id", "") or "")
    return (populated, updated_at, identifier)


def _select_preferred_system_config(records: list[SystemConfig]) -> SystemConfig | None:
    if not records:
        return None
    selected = max(records, key=_config_rank)
    if len(records) > 1:
        log.warning(
            "Multiple SystemConfig rows detected; selected id=%s updated_at=%s from %d rows",
            getattr(selected, "id", None),
            getattr(selected, "updated_at", None),
            len(records),
        )
    return selected


def _default_for_column(column) -> object | None:
    default = column.default
    if default is None:
        return None
    arg = default.arg
    if callable(arg):
        try:
            return arg()
        except TypeError:
            return None
    return arg


def _system_config_defaults() -> SystemConfig:
    sys_conf = SystemConfig()
    for column in SystemConfig.__table__.columns:
        if getattr(sys_conf, column.key, None) is not None:
            continue
        default = _default_for_column(column)
        if default is not None:
            setattr(sys_conf, column.key, default)
    return sys_conf


def _mark_schema_drift(sys_conf: SystemConfig) -> SystemConfig:
    setattr(sys_conf, _SCHEMA_DRIFT_MARKER, True)
    return sys_conf


def system_config_schema_is_behind(sys_conf: SystemConfig | None) -> bool:
    return bool(sys_conf and getattr(sys_conf, _SCHEMA_DRIFT_MARKER, False))


def _is_system_config_schema_drift(exc: SQLAlchemyError) -> bool:
    message = str(exc).lower()
    if _SYSTEM_CONFIG_TABLE not in message:
        return False
    return any(
        needle in message
        for needle in (
            "undefinedcolumn",
            "does not exist",
            "no such column",
            "has no column named",
            "unknown column",
        )
    )


def _existing_system_config_columns(db: Session) -> set[str]:
    inspector = sa.inspect(db.connection())
    return {col["name"] for col in inspector.get_columns(_SYSTEM_CONFIG_TABLE)}


def existing_system_config_columns(db: Session) -> set[str]:
    return _existing_system_config_columns(db)


def missing_system_config_columns(db: Session) -> set[str]:
    return set(SystemConfig.__table__.columns.keys()) - _existing_system_config_columns(db)


def _query_system_configs(db: Session) -> list[SystemConfig]:
    return db.query(SystemConfig).all()


def _load_system_config_fallback(db: Session, exc: SQLAlchemyError) -> SystemConfig:
    db.rollback()
    try:
        from web.db import db_migrate
        db_migrate()
        db.rollback()
        try:
            repaired = _select_preferred_system_config(_query_system_configs(db))
        except SQLAlchemyError as retry_exc:
            if not _is_system_config_schema_drift(retry_exc):
                raise
            db.rollback()
            log.warning("SystemConfig schema repair did not fully resolve drift: %s", retry_exc)
        else:
            return repaired
    except Exception as repair_exc:
        db.rollback()
        log.warning("SystemConfig schema repair failed: %s", repair_exc)

    sys_conf = _system_config_defaults()
    try:
        existing_columns = _existing_system_config_columns(db)
    except Exception as inspect_exc:
        log.warning("SystemConfig schema drift detected and inspection failed: %s", inspect_exc)
        return _mark_schema_drift(sys_conf)

    selectable = [
        SystemConfig.__table__.c[column_name]
        for column_name in SystemConfig.__table__.columns.keys()
        if column_name in existing_columns
    ]
    if selectable:
        rows = db.execute(sa.select(*selectable)).mappings().all()
        if rows:
            best_row = max(rows, key=_config_rank)
            for key, value in best_row.items():
                setattr(sys_conf, key, value)

    log.warning("SystemConfig schema drift detected; using compatibility defaults: %s", exc)
    return _mark_schema_drift(sys_conf)


def load_system_config(db: Session, *, create_if_missing: bool = False) -> SystemConfig | None:
    try:
        sys_conf = _select_preferred_system_config(_query_system_configs(db))
    except SQLAlchemyError as exc:
        if not _is_system_config_schema_drift(exc):
            raise
        return _load_system_config_fallback(db, exc)

    if not sys_conf and create_if_missing:
        sys_conf = SystemConfig()
        db.add(sys_conf)
        db.commit()
        db.refresh(sys_conf)
    return sys_conf


def save_system_config(db: Session, sys_conf: SystemConfig) -> SystemConfig:
    if not system_config_schema_is_behind(sys_conf):
        db.add(sys_conf)
        db.commit()
        db.refresh(sys_conf)
        return sys_conf

    existing_columns = _existing_system_config_columns(db)
    writable_columns = [
        column.name
        for column in SystemConfig.__table__.columns
        if column.name != "id" and column.name in existing_columns
    ]
    row_id = getattr(sys_conf, "id", None) or str(uuid4())

    table = SystemConfig.__table__
    existing_row = None
    if "id" in existing_columns:
        existing_row = db.execute(
            sa.select(table.c.id).where(table.c.id == row_id).limit(1)
        ).scalar_one_or_none()
        if existing_row is None:
            existing_row = db.execute(sa.select(table.c.id).limit(1)).scalar_one_or_none()
            if existing_row is not None:
                row_id = str(existing_row)

    values = {column_name: getattr(sys_conf, column_name, None) for column_name in writable_columns}

    if "id" in existing_columns and existing_row is not None:
        db.execute(
            sa.update(table)
            .where(table.c.id == row_id)
            .values(**values)
        )
    else:
        insert_values = dict(values)
        if "id" in existing_columns:
            insert_values["id"] = row_id
        db.execute(sa.insert(table).values(**insert_values))

    db.commit()
    log.warning(
        "Saved SystemConfig in compatibility mode using existing columns only; unsupported columns remain pending schema sync"
    )
    refreshed = load_system_config(db)
    return refreshed or sys_conf
