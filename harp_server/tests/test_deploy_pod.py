from types import SimpleNamespace

from deploy.deploy_pod import create_pod_current


class Response:
    def __init__(self, status, result=None, message=""):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.content = b"{}"
        self.reason = message
        self._payload = {"result": result} if self.ok else {"message": message}

    def json(self):
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def client(*responses):
    return SimpleNamespace(
        base_url="https://example.tapis.io",
        access_token=SimpleNamespace(access_token="test-token"),
        requests_session=Session(responses),
        verify=True,
    )


def test_create_pod_current_preserves_new_secret_fields():
    tapis = client(Response(200, {"pod_id": "harp"}))
    definition = {
        "pod_id": "harp",
        "secret_map": {"OSC_SSH_KEY": "${secret:osc_ssh_private_key}"},
        "volume_mounts": {
            "/run/key": {
                "type": "ephemeral",
                "config_content": "${pods:secrets:OSC_SSH_KEY}",
                "config_permissions": "0600",
            }
        },
    }

    result = create_pod_current(tapis, definition, timeout=1)

    assert result == {"pod_id": "harp"}
    _, request = tapis.requests_session.calls[0]
    assert request["json"] == definition
    assert request["headers"]["X-Tapis-Token"] == "test-token"


def test_create_pod_current_retries_backend_cleanup(monkeypatch):
    monkeypatch.setattr("deploy.deploy_pod.time.sleep", lambda _: None)
    tapis = client(Response(502, message="cleanup in progress"), Response(200, {"pod_id": "harp"}))

    result = create_pod_current(tapis, {"pod_id": "harp"}, timeout=1)

    assert result == {"pod_id": "harp"}
    assert len(tapis.requests_session.calls) == 2
