import ast
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "qoder_daily.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def load_functions(*names, **globals_):
    namespace = {"__builtins__": __builtins__, **globals_}
    body = [FUNCTIONS[name] for name in names]
    module = ast.Module(body=body, type_ignores=[])
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class CampaignCoreTests(unittest.TestCase):
    def test_campaigns_url_has_no_force_refresh_query(self):
        captured = {}

        def request(method, url, headers=None, retries=3):
            captured["method"] = method
            captured["url"] = url
            return FakeResponse()

        module = load_functions(
            "fetch_campaigns",
            CAMPAIGNS_PATH="/sash/api/v1/me/campaigns",
            risky_sash_headers=lambda account, uid="": ({}, None),
            request=request,
            refresh_account=lambda account: None,
        )
        account = {"base": "https://example.test", "refresh": ""}
        module["fetch_campaigns"](account)
        self.assertEqual(
            "https://example.test/sash/api/v1/me/campaigns",
            captured["url"],
        )
        self.assertNotIn("forceRefresh", captured["url"])

    def test_claim_response_matrix(self):
        module = load_functions(
            "claim_response_verdict", "first_text", "amount_of",
            json_of=lambda response: response.payload,
            CLAIM_FAILURE_TEXT={
                "REDEMPTION_CODE_OUT_OF_STOCK": "今日名额已发完，等待下一轮",
                "SAME_PERSON_ALREADY_CLAIMED": "同一设备身份下的其他账号已领取",
            },
        )
        cases = [
            (200, {"data": {"status": "CLAIMED"}}, "claimed"),
            (200, {"status": "GRANTED"}, "claimed"),
            (200, {"status": "SUCCESS"}, "claimed"),
            (200, {"status": "CLAIMED", "replayed": True}, "already"),
            (409, {"errorCode": "ALREADY_CLAIMED"}, "already"),
            (409, {"errorCode": "OTHER_CONFLICT"}, "error"),
            (200, {"status": "BLOCKED",
                   "failureCode": "SAME_PERSON_ALREADY_CLAIMED"}, "blocked"),
            (200, {"failureCode": "REDEMPTION_CODE_OUT_OF_STOCK"}, "error"),
        ]
        for status, payload, expected in cases:
            verdict, _amount, _message = module["claim_response_verdict"](
                FakeResponse(status, payload))
            self.assertEqual(expected, verdict, payload)

    def test_discover_pro_uses_hidden_reward_probe(self):
        rows = [
            {"campaignId": "daily", "campaignKey": "act-daily",
             "actionType": "CLAIM_BENEFIT", "claimStatus": "CLAIMABLE",
             "benefit": {"kind": "CREDITS", "amount": 100}},
            {"campaignId": "hidden-pro", "campaignKey": "act-hidden",
             "actionType": "VIEW_DETAILS", "claimStatus": "CLAIMABLE"},
        ]
        module = load_functions(
            "first_text", "amount_of", "campaign_benefit",
            "campaign_looks_like_pro_upgrade", "campaign_id_of",
            "discover_pro_campaign",
            fetch_campaign_reward=lambda account, campaign_id: (
                {"kind": "CREDITS", "amount": 1800}
                if campaign_id == "hidden-pro" else
                {"kind": "CREDITS", "amount": 100}),
        )
        target, claimed, probes = module["discover_pro_campaign"]({}, rows)
        self.assertEqual("hidden-pro", target["campaignId"])
        self.assertIsNone(claimed)
        self.assertTrue(any("hidden-pro" in item for item in probes))

    def test_daily_pick_excludes_pro_row_and_accepts_empty_action_type(self):
        rows = [
            {"campaignId": "pro", "campaignKey": "pro-upgrade",
             "actionType": "CLAIM_BENEFIT", "claimStatus": "CLAIMABLE",
             "benefit": {"kind": "CREDITS", "amount": 1800}},
            {"campaignId": "daily", "campaignKey": "act-daily",
             "actionType": "", "claimStatus": "CLAIMABLE",
             "benefit": {"kind": "CREDITS", "amount": 100}},
        ]
        module = load_functions(
            "first_text", "amount_of", "campaign_benefit",
            "campaign_looks_like_pro_upgrade", "pick_daily_campaign",
        )
        target, claimed = module["pick_daily_campaign"](rows)
        self.assertEqual("daily", target["campaignId"])
        self.assertFalse(claimed)

    def test_global_skips_pro_without_request(self):
        def unexpected(*args, **kwargs):
            raise AssertionError("global must not request campaigns for Pro")

        module = load_functions(
            "do_pro_upgrade",
            fetch_campaign_rows=unexpected,
        )
        result = module["do_pro_upgrade"]({"region": "global"})
        self.assertEqual((True, False, "国际版暂无 Pro 升级活动"), result)

    def test_pro_claim_uses_quota_delta(self):
        rows = [{"campaignId": "pro-1"}]
        target = {
            "campaignId": "pro-1", "campaignKey": "pro-upgrade",
            "actionType": "CLAIM_BENEFIT", "claimStatus": "CLAIMABLE",
            "benefit": {"kind": "CREDITS", "amount": 1800},
        }
        quotas = [100, 1900]

        module = load_functions(
            "do_pro_upgrade",
            fetch_campaign_rows=lambda account: (rows, FakeResponse(), None, ""),
            discover_pro_campaign=lambda account, seen: (target, None, []),
            campaign_id_of=lambda row: "pro-1",
            quota_total_remaining=lambda account: quotas.pop(0),
            claim_campaign=lambda account, campaign_id: FakeResponse(
                200, {"status": "CLAIMED"}),
            claim_response_verdict=lambda response: ("claimed", 1800, "领取成功"),
            time=type("Time", (), {"sleep": staticmethod(lambda seconds: None)}),
        )
        ok, newly_claimed, message = module["do_pro_upgrade"]({"region": "cn"})
        self.assertTrue(ok)
        self.assertTrue(newly_claimed)
        self.assertIn("+1800", message)

    def test_daily_window_boundaries(self):
        class FakeDateTime:
            current = None

            @classmethod
            def now(cls, tz=None):
                return cls.current

        module = load_functions(
            "daily_window_active", datetime=FakeDateTime, CN_TZ=object())
        for hour, minute, expected in (
                (9, 59, False), (10, 0, True), (10, 29, True),
                (10, 30, False), (11, 0, False)):
            FakeDateTime.current = type("Now", (), {"hour": hour, "minute": minute})()
            self.assertEqual(expected, module["daily_window_active"]())

    def test_window_retry_defaults_on_and_can_be_disabled(self):
        enabled_module = load_functions(
            "daily_window_retry_enabled", _env=lambda name: "")
        self.assertTrue(enabled_module["daily_window_retry_enabled"]())
        disabled_module = load_functions(
            "daily_window_retry_enabled", _env=lambda name: "0")
        self.assertFalse(disabled_module["daily_window_retry_enabled"]())

    def test_window_retry_fetches_until_daily_row_appears(self):
        daily = {
            "campaignId": "daily-1", "campaignKey": "act-daily",
            "actionType": "CLAIM_BENEFIT", "claimStatus": "CLAIMABLE",
            "benefit": {"kind": "CREDITS", "amount": 100},
        }
        responses = [[], [daily]]
        calls = {"fetch": 0, "sleep": 0}

        def fetch_rows(account):
            calls["fetch"] += 1
            return responses.pop(0), FakeResponse(), None, ""

        def sleep(seconds):
            calls["sleep"] += 1

        module = load_functions(
            "first_text", "amount_of", "campaign_benefit",
            "campaign_looks_like_pro_upgrade", "campaign_id_of",
            "pick_daily_campaign", "do_checkin",
            fetch_campaign_rows=fetch_rows,
            daily_window_retry_enabled=lambda: True,
            daily_window_active=lambda: True,
            _env=lambda name: {"QODER_WINDOW_RETRY_MAX": "2",
                               "QODER_WINDOW_RETRY_GAP": "1"}.get(name, ""),
            log=lambda message: None,
            time=type("Time", (), {"sleep": staticmethod(sleep)}),
            claim_campaign=lambda account, campaign_id: FakeResponse(
                200, {"status": "CLAIMED"}),
            claim_response_verdict=lambda response: ("claimed", 100, "领取成功"),
            json_of=lambda response: response.payload,
        )
        ok, message = module["do_checkin"]({"region": "cn"})
        self.assertTrue(ok)
        self.assertIn("100", message)
        self.assertEqual(2, calls["fetch"])
        self.assertEqual(1, calls["sleep"])


if __name__ == "__main__":
    unittest.main()
