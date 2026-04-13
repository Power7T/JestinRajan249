import logging

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from web.models import TenantConfig

log = logging.getLogger(__name__)

_SCHEMA_DRIFT_MARKER = "_schema_drift_fallback"
_TENANT_CONFIG_TABLE = "tenant_configs"


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


def _tenant_config_defaults(tenant_id: str) -> TenantConfig:
    cfg = TenantConfig(tenant_id=tenant_id)
    for column in TenantConfig.__table__.columns:
        if getattr(cfg, column.key, None) is not None:
            continue
        default = _default_for_column(column)
        if default is not None:
            setattr(cfg, column.key, default)
    return cfg


def _mark_schema_drift(cfg: TenantConfig) -> TenantConfig:
    setattr(cfg, _SCHEMA_DRIFT_MARKER, True)
    return cfg


def tenant_config_schema_is_behind(cfg: TenantConfig | None) -> bool:
    return bool(cfg and getattr(cfg, _SCHEMA_DRIFT_MARKER, False))


def _is_tenant_config_schema_drift(exc: SQLAlchemyError) -> bool:
    message = str(exc).lower()
    if _TENANT_CONFIG_TABLE not in message:
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


def _existing_tenant_config_columns(db: Session) -> set[str]:
    inspector = sa.inspect(db.connection())
    return {col["name"] for col in inspector.get_columns(_TENANT_CONFIG_TABLE)}


def _try_schema_repair(db: Session) -> bool:
    try:
        from web.db import db_migrate
        db_migrate()
        db.rollback()
        return True
    except Exception as repair_exc:
        db.rollback()
        log.warning("TenantConfig schema repair failed: %s", repair_exc)
        return False


def _load_tenant_config_fallback(db: Session, tenant_id: str, exc: SQLAlchemyError) -> TenantConfig | None:
    db.rollback()
    if _try_schema_repair(db):
        try:
            return db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        except SQLAlchemyError as retry_exc:
            if not _is_tenant_config_schema_drift(retry_exc):
                raise
            db.rollback()
            log.warning("TenantConfig schema repair did not fully resolve drift: %s", retry_exc)

    cfg = _tenant_config_defaults(tenant_id)
    try:
        existing_columns = _existing_tenant_config_columns(db)
    except Exception as inspect_exc:
        log.warning("TenantConfig schema drift detected and inspection failed: %s", inspect_exc)
        return _mark_schema_drift(cfg)

    selectable = [
        TenantConfig.__table__.c[column_name]
        for column_name in TenantConfig.__table__.columns.keys()
        if column_name in existing_columns
    ]
    if selectable:
        row = db.execute(
            sa.select(*selectable)
            .where(TenantConfig.__table__.c.tenant_id == tenant_id)
            .limit(1)
        ).mappings().first()
        if row:
            for key, value in row.items():
                setattr(cfg, key, value)
        else:
            return None

    log.warning("TenantConfig schema drift detected; using compatibility defaults: %s", exc)
    return _mark_schema_drift(cfg)


def load_tenant_config(db: Session, tenant_id: str, *, create_if_missing: bool = False) -> TenantConfig | None:
    try:
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
    except SQLAlchemyError as exc:
        if not _is_tenant_config_schema_drift(exc):
            raise
        cfg = _load_tenant_config_fallback(db, tenant_id, exc)
        if cfg is not None:
            return cfg

    if not cfg and create_if_missing:
        cfg = TenantConfig(tenant_id=tenant_id)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg
