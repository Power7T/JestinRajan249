from web.models import AutomationRule, TeamMember, Tenant, TenantConfig


def _signup_and_get_csrf(client, email="effortless@example.com"):
    resp = client.get("/login")
    csrf = resp.cookies.get("csrf_token", "")
    signup_resp = client.post(
        "/signup",
        data={"email": email, "password": "securepassword1", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert signup_resp.status_code in (302, 303)
    resp = client.get("/onboarding?step=1")
    return resp.cookies.get("csrf_token", "")


def test_quick_start_applies_effortless_defaults(client, db):
    csrf = _signup_and_get_csrf(client)

    resp = client.post(
        "/onboarding/quick-start",
        data={
            "property_names": "Sea View Loft",
            "property_city": "Goa",
            "check_in_time": "2:00 PM",
            "check_out_time": "11:00 AM",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)
    assert "/onboarding?step=5" in resp.headers.get("location", "")

    tenant = db.query(Tenant).filter_by(email="effortless@example.com").first()
    cfg = db.query(TenantConfig).filter_by(tenant_id=tenant.id).first()
    rules = db.query(AutomationRule).filter_by(tenant_id=tenant.id).all()
    members = db.query(TeamMember).filter_by(tenant_id=tenant.id).all()

    assert cfg.property_names == "Sea View Loft"
    assert cfg.property_city == "Goa"
    assert cfg.email_ingest_mode == "forwarding"
    assert cfg.house_rules
    assert cfg.faq
    assert cfg.custom_instructions
    assert cfg.escalation_email == "effortless@example.com"
    assert len(rules) == 3
    assert any(member.role == "owner" for member in members)
