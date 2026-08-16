from restore_wss.vpn import VpnConnection, interpret_failure, plan_vpn


def conn(uuid="u1", name="work", kind="vpn"):
    return VpnConnection(uuid=uuid, name=name, kind=kind)


def test_only_the_identity_is_stored_never_a_credential():
    stored = conn().to_json()
    assert set(stored) == {"uuid", "name", "type"}
    assert VpnConnection.from_json(stored) == conn()


def test_a_vpn_that_is_down_is_reconnected():
    plan = plan_vpn([conn()], active_uuids=[], known_uuids=["u1"])
    assert [a.kind for a in plan.actions] == ["activate"]
    assert "reconnect work" in plan.actions[0].describe()


def test_a_vpn_that_is_already_up_is_left_alone():
    """Idempotency: restore does not tear down a working connection to raise it again."""
    plan = plan_vpn([conn()], active_uuids=["u1"], known_uuids=["u1"])
    assert [a.kind for a in plan.actions] == ["already-up"]
    assert "already connected" in plan.actions[0].describe()


def test_a_connection_networkmanager_no_longer_knows_is_reported_not_attempted():
    plan = plan_vpn([conn(uuid="gone")], active_uuids=[], known_uuids=["u1"])
    assert plan.actions == []
    assert plan.missing[0].uuid == "gone"
    assert "no longer a NetworkManager connection" in plan.describe()[0]


def test_wireguard_counts_as_a_vpn():
    plan = plan_vpn([conn(kind="wireguard")], active_uuids=[], known_uuids=["u1"])
    assert plan.actions[0].connection.kind == "wireguard"


def test_a_connection_that_needs_a_password_is_a_prompt_not_a_failure():
    message = interpret_failure(
        "GDBus.Error:org.freedesktop.NetworkManager.Error.NoSecrets: No agents were available"
    )
    assert "password or a code" in message
    assert "connect it from the network menu" in message


def test_other_failures_are_passed_through_and_named():
    assert "not authorised" in interpret_failure("Not authorized to control networking.")
    assert interpret_failure("interface not available") == "interface not available"


def test_the_restore_reports_a_vpn_that_needs_a_password_as_needing_the_user():
    """Not a failure: a VPN wanting 2FA is a prompt, and calling it a failure trains people to
    ignore the report."""
    from restore_wss.model import Snapshot
    from restore_wss.plan import build_plan
    from restore_wss.restore import execute

    class Core:
        def ensure_workspaces(self, count):
            return count

        def activate_workspace(self, index):
            pass

    class Nm:
        def activate(self, uuid):
            raise RuntimeError("org.freedesktop.NetworkManager.Error.NoSecrets: No agents")

    plan = build_plan(Snapshot(), [])
    plan.vpn = plan_vpn([conn()], active_uuids=[], known_uuids=["u1"])
    result = execute(plan, Core(), wait_seconds=1, sleep=lambda _s: None, network_manager=Nm())

    assert result.vpn == [("work", "needs-you", interpret_failure("NoSecrets: No agents"))]
    assert not result.failures


def test_a_vpn_that_is_up_is_not_touched_by_the_restore():
    from restore_wss.model import Snapshot
    from restore_wss.plan import build_plan
    from restore_wss.restore import execute

    calls = []

    class Core:
        def ensure_workspaces(self, count):
            return count

        def activate_workspace(self, index):
            pass

    class Nm:
        def activate(self, uuid):
            calls.append(uuid)

    plan = build_plan(Snapshot(), [])
    plan.vpn = plan_vpn([conn()], active_uuids=["u1"], known_uuids=["u1"])
    result = execute(plan, Core(), wait_seconds=1, sleep=lambda _s: None, network_manager=Nm())

    assert calls == []
    assert result.vpn == [("work", "done", "already connected")]
