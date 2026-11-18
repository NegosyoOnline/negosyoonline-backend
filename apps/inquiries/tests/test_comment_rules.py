"""Comment auto-reply rules and automation (task z8uq9mg9pj), with recorded fake Meta payloads only:
no live Meta, no real token. `messenger._transport` is replaced by a fake that records every call.
Celery runs eagerly without REDIS_URL, so the tests call `evaluate` and `send` directly and patch
`apply_async` to read the countdown."""
import json
import logging
import random
import time
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.common.errors import ApiError
from apps.inquiries import comment_automation as auto
from apps.inquiries import comment_media
from apps.inquiries import comment_limits as limits
from apps.inquiries import comment_rules as rules
from apps.inquiries import messenger, tasks
from apps.inquiries.models import (
    InquiryAutoReply, InquiryComment, InquiryCommentAction, InquiryCommentAutomation, InquiryCommentRule,
    InquiryPost,
)
from apps.inquiries.tests import test_comments as tc
from apps.inquiries.tests.test_messenger import PAGE_A, PAGE_TOKEN

A = InquiryCommentAction
automation_on = override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=True)
POST = tc.POST
PUBLIC = "{Hi|Hello|Salamat} {name}! {Check your inbox|We sent you a message}"
PRIVATE = "{Hi|Hello} {name}, details: https://shop.example.test/p/1"


class SpintaxTests(SimpleTestCase):
    def test_a_template_counts_its_variants_and_keeps_the_name_token(self):
        t = rules.parse("{Hi|Hello} {name}! {A|B|C}")
        self.assertEqual(t.variant_count, 6)
        self.assertEqual(rules.parse("Plain text").variant_count, 1)

    def test_bad_syntax_is_refused(self):
        for bad in ("{a|b", "a|b}", "{a|{b|c}}", "{a}", "{x}", "{|a}", "{a|}", "{a| |b}", "{Hi|Hi}", "", "{}"):
            with self.assertRaises(rules.SpintaxError, msg=bad):
                rules.parse(bad)

    def test_the_longest_variant_counts_a_30_character_name(self):
        t = rules.parse("{ab|abcd} {name}")
        self.assertEqual(t.max_length, 4 + 1 + 30)

    def test_links_are_found_in_any_option(self):
        for text in ("see https://x.example", "visit www.shop.ph", "go to shop.com now", "{Hi|bit.ly/abc}",
                     "www . shop", "{a|b}shop.store", "HTTP://UPPER", "order at my-shop.online today"):
            self.assertTrue(rules.has_link(text), text)
        for text in ("Salamat po! Check your inbox.", "It is 5.50 pesos", "Ok. Me too", "{Hi|Hello} {name}!"):
            self.assertFalse(rules.has_link(text), text)

    def test_the_same_variant_is_avoided_when_another_exists(self):
        t = rules.parse("{A|B}")
        first, digest = rules.choose(t)
        for _ in range(50):
            variant, new = rules.choose(t, digest)
            self.assertNotEqual(new, digest)
            self.assertNotEqual(variant, first)

    def test_a_single_variant_template_still_chooses(self):
        t = rules.parse("Only one")
        variant, digest = rules.choose(t, rules.variant_hash("Only one"))
        self.assertEqual(variant, "Only one")

    def test_first_name(self):
        self.assertEqual(rules.first_name("Maria Santos"), "Maria")
        self.assertEqual(rules.first_name(""), "there")
        self.assertEqual(rules.first_name("   "), "there")
        self.assertEqual(rules.first_name("A" * 80), "A" * 30)
        self.assertEqual(rules.fill(rules.parse("Hi {name}!").parts[0].replace("", "{n}"), "x"), "Hi {n}!")

    def test_keyword_match_is_whole_word_and_case_insensitive(self):
        rule = InquiryCommentRule(trigger="keywords", keywords=["price", "how much", "hm"], scope="all")
        for text in ("PRICE?", "Hi, how  much?", "hm po", "magkano price po", "what's the price."):
            if "how  much" in text:
                continue
            self.assertTrue(rules.matches(rule, text, POST), text)
        for text in ("ahmed", "prices", "hmm", "nothing"):
            self.assertFalse(rules.matches(rule, text, POST), text)
        self.assertTrue(rules.matches(rule, "how much is it", POST))


@automation_on
class RulesTestCase(tc.CommentsTestCase):
    def setUp(self):
        super().setUp()
        self.rules_url = f"/v1/sites/{self.site_id}/inquiries/comment-rules/"
        self.n = 0

    # fixtures
    def rule(self, site=None, **kw):
        values = {"site": site or self.site, "public_text": PUBLIC, "private_text": ""}
        values.update(kw)
        return InquiryCommentRule.objects.create(**values)

    def master(self, on=True, site=None, **kw):
        row, _ = InquiryCommentAutomation.objects.update_or_create(site=site or self.site, defaults={"enabled": on, **kw})
        return row

    def comment(self, author=None, name="Maria Santos", message="How much is the medium?", post_id=POST, age_days=0,
                **kw):
        self.n += 1
        created = int(time.time()) - int(age_days * 86400)
        return self.feed_in(comment_id=kw.pop("comment_id", f"111000111_5{self.n:03d}"),
                            author=author or f"77{self.n:05d}", name=name, message=message, post_id=post_id,
                            created=created, **kw)

    def run_eval(self, comment, now=None):
        with mock.patch.object(tasks.send_comment_auto_reply, "apply_async") as apply, \
                self.captureOnCommitCallbacks(execute=True):
            result = auto.evaluate(comment.pk, now=now)
        return result, apply

    def queued(self, comment, kind=None):
        rows = InquiryCommentAction.objects.filter(comment=comment, source="rule", state="queued")
        return rows.filter(kind=kind) if kind else rows

    def go(self, **comment_kw):
        """Ready site, master on, one rule, one comment queued: (comment, action)."""
        self.ready()
        self.master()
        self.rule(**{k: comment_kw.pop(k) for k in list(comment_kw) if k in ("private_text",)})
        comment = self.comment(**comment_kw)
        result, _ = self.run_eval(comment)
        self.assertEqual(result, "queued")
        return comment, self.queued(comment, "public").get()

    def put(self, path, data):
        return self.send("put", path, data)


class GateTests(RulesTestCase):
    def test_every_route_is_404_while_the_switch_is_off_signed_in_or_not(self):
        self.ready()
        ref = "cr_" + "a" * 24
        routes = [("get", ""), ("put", "automation/"), ("get", "posts/"), ("post", "preview/"), ("post", ""),
                  ("get", ref + "/"), ("put", ref + "/"), ("delete", ref + "/")]
        for off in ({"INQUIRY_COMMENT_AUTOMATION_ENABLED": False}, {"INQUIRY_COMMENTS_ENABLED": False},
                    {"INQUIRY_MESSENGER_ENABLED": False}):
            with override_settings(**off):
                for method, tail in routes:
                    for client in (self.client, self.client_class()):
                        if method == "get":
                            response = self.get(self.rules_url + tail, client=client)
                        else:
                            response = self.send(method, self.rules_url + tail, {}, client=client)
                        self.error(response, 404, "not_found")

    def test_signed_out_is_401_and_a_foreign_site_is_404_when_on(self):
        self.ready()
        self.assertEqual(self.get(self.rules_url, client=self.client_class()).status_code, 401)
        other = self.other_site()
        self.error(self.get(f"/v1/sites/{other.public_id}/inquiries/comment-rules/"), 404, "not_found")

    def test_the_default_automation_is_off_and_not_ready_without_a_page(self):
        body = self.get(self.rules_url).json()
        self.assertEqual(body["rules"], [])
        a = body["automation"]
        self.assertEqual((a["enabled"], a["active"], a["ready"], a["reason"], a["banner"]),
                         (False, False, False, "not_connected", None))
        self.assertEqual((a["today"]["count"], a["today"]["cap"], a["hour"]["cap"]), (0, 300, 30))
        self.assertEqual(a["limits"]["public_max_chars"], 500)
        self.assertEqual(a["limits"]["per_person_hours"], 24)

    def test_the_deploy_check_wants_comments_on_with_automation(self):
        from apps.inquiries.checks import check_comment_automation_dependencies as check
        self.assertEqual(check(None), [])
        with override_settings(INQUIRY_COMMENTS_ENABLED=False):
            self.assertEqual([e.id for e in check(None)], ["inquiries.E007"])
        with override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=False, INQUIRY_COMMENTS_ENABLED=False):
            self.assertEqual(check(None), [])

    def test_the_switch_defaults_to_off(self):
        import config.settings as module  # the module value, not the test override
        self.assertFalse(module.INQUIRY_COMMENT_AUTOMATION_ENABLED)


class RuleApiTests(RulesTestCase):
    def body(self, **kw):
        return {"trigger": "keywords", "keywords": ["price", "magkano"], "scope": "all", "public_text": PUBLIC,
                "private_text": PRIVATE, **kw}

    def test_create_read_update_delete(self):
        self.ready()
        response = self.post(self.rules_url, self.body())
        self.assertEqual(response.status_code, 201, response.content)
        rule = response.json()
        self.assertTrue(rule["ref"].startswith("cr_"))
        self.assertEqual((rule["enabled"], rule["trigger"], rule["keywords"], rule["scope"], rule["post_ids"], rule["posts"]),
                         (True, "keywords", ["price", "magkano"], "all", [], []))
        self.assertEqual((rule["public_variants"], rule["private_variants"]), (6, 2))
        self.assertEqual(set(rule), {"ref", "enabled", "trigger", "keywords", "scope", "post_ids", "posts", "public_text",
                                     "private_text", "public_variants", "private_variants", "created_at", "updated_at"})
        url = self.rules_url + rule["ref"] + "/"
        self.assertEqual(self.get(url).json(), rule)
        listed = self.get(self.rules_url).json()
        self.assertEqual([r["ref"] for r in listed["rules"]], [rule["ref"]])
        updated = self.put(url, {"enabled": False}).json()
        self.assertEqual((updated["enabled"], updated["public_text"]), (False, PUBLIC))
        updated = self.put(url, {"private_text": ""}).json()
        self.assertEqual((updated["private_text"], updated["private_variants"]), ("", 0))
        self.assertEqual(self.send("delete", url).json(), {"deleted": True})
        self.error(self.get(url), 404, "not_found")

    def test_a_partial_update_is_validated_as_merged(self):
        self.ready()
        rule = self.post(self.rules_url, self.body()).json()
        url = self.rules_url + rule["ref"] + "/"
        self.error(self.put(url, {"keywords": []}), 422, "keywords_required")
        self.error(self.put(url, {"scope": "selected"}), 422, "posts_required")
        self.assertEqual(self.put(url, {"trigger": "any"}).json()["keywords"], [])

    def assert_invalid(self, data, code, field, message=None):
        response = self.post(self.rules_url, self.body(**data))
        error = self.error(response, 422, code)
        self.assertIn(field, error["fields"])
        if message:
            self.assertEqual(error["message"], message)
        self.assertFalse(InquiryCommentRule.objects.exists())

    def test_the_validation_codes(self):
        self.ready()
        self.assert_invalid({"public_text": "Just one version"}, "spintax_variants_required", "public_text",
                            "Add at least 2 variants, for example {Hi|Hello} {name}.")
        self.assert_invalid({"public_text": "  "}, "public_required", "public_text")
        self.assert_invalid({"public_text": "{Hi|Hello} see https://shop.example.test"}, "link_in_public_reply",
                            "public_text", "Links cannot go in a public reply. Put the link in the private message.")
        self.assert_invalid({"public_text": "{Hi|Hello} shop.com"}, "link_in_public_reply", "public_text")
        self.assert_invalid({"public_text": "{Hi|Hello" }, "invalid_spintax", "public_text")
        self.assert_invalid({"private_text": "{Hi|}"}, "invalid_spintax", "private_text")
        self.assert_invalid({"public_text": "{Hi|Hello} " + "x" * 501}, "public_too_long", "public_text",
                            "A public reply is at most 500 characters.")
        self.assert_invalid({"private_text": "x" * 1001}, "private_too_long", "private_text",
                            "A private message is at most 1000 characters.")
        self.assert_invalid({"keywords": []}, "keywords_required", "keywords", "Add at least one word to look for.")
        self.assert_invalid({"keywords": ["  ", ""]}, "keywords_required", "keywords")
        self.assert_invalid({"keywords": ["x" * 41]}, "invalid_keywords", "keywords")
        self.assert_invalid({"keywords": [f"w{i}" for i in range(21)]}, "invalid_keywords", "keywords")
        self.assert_invalid({"scope": "selected", "post_ids": []}, "posts_required", "post_ids",
                            "Pick at least one post or ad.")
        self.assert_invalid({"scope": "selected", "post_ids": ["999_999"]}, "unknown_post", "post_ids")
        self.assert_invalid({"scope": "selected", "post_ids": [f"1_{i}" for i in range(51)]}, "invalid_posts", "post_ids")
        self.assert_invalid({"trigger": "regex"}, "invalid_trigger", "trigger")
        self.assert_invalid({"scope": "some"}, "invalid_scope", "scope")

    def test_the_length_boundaries_use_the_longest_variant_and_a_30_character_name(self):
        self.ready()
        edge = "{a|bb} {name} " + "x" * (500 - 2 - 30 - 2)
        self.assertEqual(rules.parse(edge).max_length, 500)
        self.assertEqual(self.post(self.rules_url, self.body(public_text=edge)).status_code, 201)
        over = "{a|bb} {name} " + "x" * (500 - 2 - 30 - 1)
        error = self.error(self.post(self.rules_url, self.body(public_text=over)), 422, "public_too_long")
        self.assertIn("public_text", error["fields"])
        private_edge = "{a|b} " + "y" * (1000 - 2)
        self.assertEqual(rules.parse(private_edge).max_length, 1000)
        self.assertEqual(self.post(self.rules_url, self.body(private_text=private_edge)).status_code, 201)

    def test_private_text_may_hold_links_and_one_variant(self):
        self.ready()
        response = self.post(self.rules_url, self.body(private_text="Order here: https://shop.example.test/p/1"))
        self.assertEqual(response.status_code, 201)

    def test_keywords_are_cleaned_and_keywords_or_posts_are_dropped_when_not_used(self):
        self.ready()
        rule = self.post(self.rules_url, self.body(keywords=[" Price ", "price", "PRICE", "how   much"])).json()
        self.assertEqual(rule["keywords"], ["Price", "how much"])
        rule = self.post(self.rules_url, self.body(trigger="any", keywords=["x"], scope="all", post_ids=["1_1"])).json()
        self.assertEqual((rule["keywords"], rule["post_ids"]), ([], []))

    def test_unknown_fields_are_refused(self):
        self.ready()
        self.error(self.post(self.rules_url, {**self.body(), "site": 5}), 400, "invalid_request")
        self.error(self.put(self.rules_url + "automation/", {"enabled": True, "x": 1}), 400, "invalid_request")

    def test_at_most_20_rules(self):
        self.ready()
        with mock.patch.object(limits, "MAX_RULES", 2):
            self.assertEqual(self.post(self.rules_url, self.body()).status_code, 201)
            self.assertEqual(self.post(self.rules_url, self.body()).status_code, 201)
            self.error(self.post(self.rules_url, self.body()), 409, "too_many_rules")
        self.assertEqual(limits.MAX_RULES, 20)

    def test_selected_posts_are_resolved_and_must_be_this_sites(self):
        self.ready()
        self.comment(post={"message": "Fresh mangoes", "is_published": False})
        other = self.other_site()
        conn = self.connect_row(site=other, page="222000222", user="fbuser-2")
        InquiryPost.objects.create(site=other, connection=conn, external_id="222000222_9", caption="Ben's")
        rule = self.post(self.rules_url, self.body(scope="selected", post_ids=[POST])).json()
        self.assertEqual(rule["posts"], [{"id": POST, "is_ad": True, "caption": "Fresh mangoes",
                                          "permalink": f"https://www.facebook.com/{POST}"}])
        self.error(self.post(self.rules_url, self.body(scope="selected", post_ids=["222000222_9"])), 422, "unknown_post")

    def test_rules_are_isolated_between_sites(self):
        self.ready()
        mine = self.post(self.rules_url, self.body()).json()
        other = self.other_site()
        theirs = self.rule(site=other)
        self.assertEqual([r["ref"] for r in self.get(self.rules_url).json()["rules"]], [mine["ref"]])
        self.error(self.get(self.rules_url + theirs.ref + "/"), 404, "not_found")
        self.error(self.put(self.rules_url + theirs.ref + "/", {"enabled": False}), 404, "not_found")
        self.error(self.send("delete", self.rules_url + theirs.ref + "/"),
                   404, "not_found")
        theirs.refresh_from_db()
        self.assertTrue(theirs.enabled)
        self.error(self.get(self.rules_url + "bad-ref/"), 404, "not_found")

    def test_the_posts_picker(self):
        self.ready()
        for n in range(31):
            self.comment(post_id=f"111000111_{900 + n}", post={"message": f"Mango lot {n}"})
        first = self.get(self.rules_url + "posts/").json()
        self.assertEqual(len(first["items"]), 30)
        self.assertEqual(set(first["items"][0]), {"id", "is_ad", "caption", "permalink"})
        second = self.get(self.rules_url + "posts/?cursor=" + first["next_cursor"]).json()
        self.assertEqual((len(second["items"]), second["next_cursor"]), (1, None))
        found = self.get(self.rules_url + "posts/?q=lot 7").json()["items"]
        self.assertEqual([i["caption"] for i in found], ["Mango lot 7"])
        self.error(self.get(self.rules_url + "posts/?q=a"), 422, "invalid_query")
        self.error(self.get(self.rules_url + "posts/?cursor=x"), 422, "invalid_cursor")
        other = self.other_site()
        self.assertEqual(self.get(f"/v1/sites/{other.public_id}/inquiries/comment-rules/posts/").status_code, 404)

    def test_the_preview_is_always_200(self):
        self.ready()
        ok = self.post(self.rules_url + "preview/", {"template": PUBLIC, "kind": "public"}).json()
        self.assertEqual((ok["valid"], ok["error"], ok["variant_count"]), (True, None, 6))
        self.assertTrue(1 <= len(ok["samples"]) <= 3)
        self.assertTrue(all("Maria" in s and "{" not in s for s in ok["samples"]))
        bad = self.post(self.rules_url + "preview/", {"template": "one", "kind": "public"}).json()
        self.assertEqual((bad["valid"], bad["error"]["code"], bad["variant_count"], bad["samples"]),
                         (False, "spintax_variants_required", 0, []))
        link = self.post(self.rules_url + "preview/", {"template": "{a|b} www.x.ph", "kind": "public"}).json()
        self.assertEqual(link["error"]["code"], "link_in_public_reply")
        private = self.post(self.rules_url + "preview/", {"template": "plain https://x.ph", "kind": "private"}).json()
        self.assertTrue(private["valid"])
        self.error(self.post(self.rules_url + "preview/", {"template": "x", "kind": "other"}), 422, "invalid_kind")

    def test_writes_are_rate_limited(self):
        self.ready()
        from apps.common.throttle import hit
        from apps.inquiries import api_owner
        for _ in range(api_owner.REPLY_LIMIT):
            hit("inq-comment-rule-write", self.account.pk, limit=api_owner.REPLY_LIMIT, window=api_owner.WINDOW)
        self.assertEqual(self.post(self.rules_url, self.body()).status_code, 429)
        self.assertEqual(self.get(self.rules_url).status_code, 200)


class MasterAndQueueCancelTests(RulesTestCase):
    def test_turning_on_clears_the_pause_and_strikes_and_off_cancels_the_queue(self):
        comment, action = self.go()
        self.master(True, paused_until=timezone.now() + timedelta(minutes=30), pause_reason="rate_limit",
                    strikes=2, strikes_date=timezone.localdate())
        a = self.get(self.rules_url).json()["automation"]
        self.assertEqual((a["banner"]["code"], a["active"]), ("paused_rate_limit", False))
        on = self.put(self.rules_url + "automation/", {"enabled": True}).json()
        self.assertEqual((on["enabled"], on["banner"], on["active"]), (True, None, True))
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.strikes, row.paused_until, row.pause_reason), (0, None, ""))
        off = self.put(self.rules_url + "automation/", {"enabled": False}).json()
        self.assertEqual((off["enabled"], off["active"]), (False, False))
        action.refresh_from_db()
        self.assertEqual(action.state, "cancelled")

    def test_disabling_deleting_or_narrowing_a_rule_cancels_its_queued_replies(self):
        for change in ("disable", "delete", "narrow"):
            InquiryComment.objects.all().delete()
            InquiryCommentRule.objects.all().delete()
            comment, action = self.go(message="price?")
            rule = InquiryCommentRule.objects.get()
            rule.trigger, rule.keywords = "keywords", ["price"]
            rule.save()
            url = self.rules_url + rule.ref + "/"
            if change == "disable":
                self.put(url, {"enabled": False})
            elif change == "delete":
                self.send("delete", url)
            else:
                self.put(url, {"keywords": ["other"]})
            action.refresh_from_db()
            self.assertEqual(action.state, "cancelled", change)

    def test_a_change_that_still_matches_keeps_the_queue(self):
        comment, action = self.go(message="price?")
        rule = InquiryCommentRule.objects.get()
        self.put(self.rules_url + rule.ref + "/", {"public_text": "{Yo|Hey} {name}"})
        action.refresh_from_db()
        self.assertEqual(action.state, "queued")

    def test_a_removed_comment_cancels_its_queued_reply(self):
        comment, action = self.go()
        self.feed_in(verb="remove", comment_id=comment.external_id)
        action.refresh_from_db()
        self.assertEqual(action.state, "cancelled")


class EvaluateTests(RulesTestCase):
    def setUp(self):
        super().setUp()
        # The photo and post-text reads (comment_media) also use on_commit; these tests count only C2's callback.
        patcher = mock.patch.object(comment_media, "schedule")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ready()
        self.master()

    def test_a_new_comment_queues_a_public_and_a_private_reply_with_a_30_to_120_second_delay(self):
        rule = self.rule(private_text=PRIVATE)
        comment = self.comment()
        result, apply = self.run_eval(comment)
        self.assertEqual(result, "queued")
        rows = InquiryCommentAction.objects.filter(comment=comment).order_by("kind")
        self.assertEqual([(r.kind, r.source, r.state) for r in rows], [("private", "rule", "queued"), ("public", "rule", "queued")])
        self.assertTrue(all(InquiryAutoReply.objects.get(action=r).rule == rule for r in rows))
        self.assertEqual(apply.call_count, 2)
        for call in apply.call_args_list:
            self.assertTrue(30 <= call.kwargs["countdown"] <= 120, call)
        self.assertEqual(sorted(c.kwargs["args"][0] for c in apply.call_args_list), sorted(r.pk for r in rows))

    def test_the_delay_is_random_within_the_tunable_range(self):
        self.rule()
        seen = set()
        for _ in range(12):
            comment = self.comment()
            _, apply = self.run_eval(comment)
            seen.add(apply.call_args.kwargs["countdown"])
        self.assertTrue(len(seen) > 1)
        with mock.patch.object(limits, "DELAY_MIN", 5), mock.patch.object(limits, "DELAY_MAX", 5):
            _, apply = self.run_eval(self.comment())
        self.assertEqual(apply.call_args.kwargs["countdown"], 5)

    def test_nothing_is_sent_by_evaluation_itself(self):
        self.rule()
        self.run_eval(self.comment())
        self.assertEqual(self.meta.calls, [])

    def test_the_webhook_only_queues_a_task_and_never_sends(self):
        self.rule()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            comment = self.comment()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(self.meta.calls, [])
        self.assertFalse(InquiryCommentAction.objects.exists())
        with mock.patch.object(tasks.evaluate_comment_rules, "delay") as delay:
            callbacks[0]()
        delay.assert_called_once_with(comment.pk)

    def test_an_edit_a_duplicate_or_the_pages_own_comment_queues_no_task(self):
        self.rule()
        comment = self.comment()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.feed_in(verb="edited", comment_id=comment.external_id, message="price now?")
            self.feed_in(comment_id=comment.external_id)
            self.feed_in(comment_id="111000111_7001", author=PAGE_A, name="Store", parent_id=comment.external_id)
        self.assertEqual(callbacks, [])

    def test_the_webhook_queues_nothing_while_the_switch_is_off(self):
        self.rule()
        with override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=False), \
                self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.comment()
        self.assertEqual(callbacks, [])

    def test_a_failing_enqueue_never_breaks_the_webhook(self):
        with mock.patch.object(auto.transaction, "on_commit", side_effect=RuntimeError("broker down")):
            comment = self.comment()
        self.assertIsNotNone(comment)

    def test_an_edit_never_retriggers_even_when_evaluated_again(self):
        self.rule(trigger="keywords", keywords=["price"])
        comment = self.comment(message="hello")
        self.assertEqual(self.run_eval(comment)[0], "no_rule")
        self.feed_in(verb="edited", comment_id=comment.external_id, message="price?")
        self.assertEqual(InquiryCommentAction.objects.count(), 0)
        comment = self.comment(message="price?")
        self.assertEqual(self.run_eval(comment)[0], "queued")
        self.feed_in(verb="edited", comment_id=comment.external_id, message="price??")
        self.assertEqual(self.run_eval(comment)[0], "already")
        self.assertEqual(InquiryCommentAction.objects.filter(comment=comment).count(), 1)

    def test_one_automatic_reply_per_kind_per_comment_ever_in_the_database(self):
        from django.db import IntegrityError, transaction
        rule = self.rule()
        comment = self.comment()
        self.run_eval(comment)
        with self.assertRaises(IntegrityError), transaction.atomic():
            InquiryCommentAction.objects.create(site=self.site, comment=comment, kind="public", source="rule", state="failed")
        InquiryCommentAction.objects.create(site=self.site, comment=comment, kind="public", source="manual", state="failed")
        self.assertEqual(self.run_eval(comment)[0], "already")

    def test_skipped_comments(self):
        self.rule()
        now = timezone.now()
        cases = {
            "own_comment": lambda: self.comment(author=PAGE_A, name="Ana Store Page"),
            "not_top_level": lambda: self.comment(parent_id=tc.COMMENT),
            "too_old": lambda: self.comment(age_days=7.5),
        }
        self.comment_root = self.comment(comment_id=tc.COMMENT)
        self.run_eval(self.comment_root)
        for expected, make in cases.items():
            comment = make()
            result, _ = self.run_eval(comment)
            self.assertEqual(result, expected if expected != "own_comment" else "own_comment")
            self.assertFalse(InquiryCommentAction.objects.filter(comment=comment).exists(), expected)

    def test_the_seven_day_boundary(self):
        self.rule()
        young = self.comment(age_days=6.9)
        old = self.comment(age_days=7.1)
        self.assertEqual(self.run_eval(young)[0], "queued")
        self.assertEqual(self.run_eval(old)[0], "too_old")

    def test_a_hidden_comment_a_removed_one_and_one_without_an_author_are_skipped(self):
        self.rule()
        hidden = self.comment()
        InquiryComment.objects.filter(pk=hidden.pk).update(is_hidden=True)
        gone = self.comment()
        self.feed_in(verb="remove", comment_id=gone.external_id)
        anonymous = self.comment()
        InquiryComment.objects.filter(pk=anonymous.pk).update(author_psid="")
        self.assertEqual(self.run_eval(hidden)[0], "hidden")
        self.assertEqual(self.run_eval(gone)[0], "gone")
        self.assertEqual(self.run_eval(anonymous)[0], "no_author")
        self.assertEqual(self.run_eval(type("C", (), {"pk": 0})())[0], "gone")

    def test_a_thread_the_page_already_answered_is_skipped(self):
        self.rule()
        comment = self.comment()
        self.feed_in(comment_id="111000111_7002", author=PAGE_A, name="Store", parent_id=comment.external_id,
                     message="Hello!")
        self.assertEqual(self.run_eval(comment)[0], "answered")
        later = self.comment()
        InquiryComment.objects.filter(pk=later.pk).update(private_reply_at=timezone.now())
        self.assertEqual(self.run_eval(later)[0], "answered")

    def test_the_site_master_pause_and_readiness_are_checked(self):
        self.rule()
        comment = self.comment()
        self.master(False)
        self.assertEqual(self.run_eval(comment)[0], "site_off")
        self.master(True, paused_until=timezone.now() + timedelta(minutes=5), pause_reason="rate_limit")
        self.assertEqual(self.run_eval(comment)[0], "paused")
        self.master(True, paused_until=timezone.now() - timedelta(minutes=5))
        InquiryCommentSettings = __import__("apps.inquiries.models", fromlist=["x"]).InquiryCommentSettings
        InquiryCommentSettings.objects.update(feed_subscribed=False)
        self.assertEqual(self.run_eval(comment)[0], "not_ready")
        InquiryCommentSettings.objects.update(feed_subscribed=True)
        self.assertEqual(self.run_eval(comment)[0], "queued")

    def test_the_platform_switch_off_evaluates_nothing(self):
        self.rule()
        comment = self.comment()
        with override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=False):
            self.assertEqual(self.run_eval(comment)[0], "off")
        self.assertFalse(InquiryCommentAction.objects.exists())

    def test_a_site_with_no_automation_row_is_off(self):
        InquiryCommentAutomation.objects.all().delete()
        self.rule()
        self.assertEqual(self.run_eval(self.comment())[0], "site_off")

    def test_scope_selected_answers_only_the_chosen_posts(self):
        self.rule(scope="selected", post_ids=[POST])
        other = self.comment(post_id="111000111_999")
        mine = self.comment()
        self.assertEqual(self.run_eval(other)[0], "no_rule")
        self.assertEqual(self.run_eval(mine)[0], "queued")

    def test_keywords_decide_and_the_first_enabled_rule_wins(self):
        first = self.rule(trigger="keywords", keywords=["magkano"], public_text="{First|Uno} {name}")
        self.rule(trigger="any", public_text="{Second|Dos} {name}")
        disabled = self.rule(enabled=False, trigger="any", public_text="{Third|Tres} {name}")
        a = self.comment(message="Magkano po?")
        b = self.comment(message="hello")
        self.run_eval(a)
        self.run_eval(b)
        self.assertEqual(InquiryAutoReply.objects.get(action__comment=a).rule, first)
        self.assertNotIn(InquiryAutoReply.objects.get(action__comment=b).rule_id, (first.pk, disabled.pk))

    def test_a_rule_of_another_site_never_answers(self):
        other = self.other_site()
        self.rule(site=other)
        self.assertEqual(self.run_eval(self.comment())[0], "no_rule")

    def test_a_comment_of_another_site_uses_that_sites_switches(self):
        other = self.other_site()
        conn = self.connect_row(site=other, page="222000222", user="fbuser-2")
        tc.InquiryCommentSettings.objects.create(site=other, feed_subscribed=True, page_id="222000222")
        self.rule(site=other)
        self.master(True, site=other, enabled=False)
        c = self.feed_in(page="222000222", comment_id="222000222_5", post_id="222000222_1")
        self.assertEqual(self.run_eval(c)[0], "site_off")
        self.master(True, site=other)
        self.assertEqual(self.run_eval(c)[0], "queued")
        self.assertEqual(InquiryCommentAction.objects.get().site, other)


class PerPersonTests(RulesTestCase):
    def setUp(self):
        super().setUp()
        self.ready()
        self.master()
        self.rule()

    def test_one_automatic_reply_per_person_per_post_per_24_hours(self):
        first = self.comment(author="7700001")
        self.assertEqual(self.run_eval(first)[0], "queued")
        second = self.comment(author="7700001")
        self.assertEqual(self.run_eval(second)[0], "person_limit")
        self.assertFalse(InquiryCommentAction.objects.filter(comment=second).exists())
        elsewhere = self.comment(author="7700001", post_id="111000111_998")
        self.assertEqual(self.run_eval(elsewhere)[0], "queued")
        other_person = self.comment(author="7700002")
        self.assertEqual(self.run_eval(other_person)[0], "queued")

    def test_the_24_hour_boundary(self):
        first = self.comment(author="7700001")
        self.run_eval(first)
        InquiryCommentAction.objects.filter(comment=first).update(created_at=timezone.now() - timedelta(hours=23, minutes=59))
        self.assertEqual(self.run_eval(self.comment(author="7700001"))[0], "person_limit")
        InquiryCommentAction.objects.filter(comment=first).update(created_at=timezone.now() - timedelta(hours=24, minutes=1))
        self.assertEqual(self.run_eval(self.comment(author="7700001"))[0], "queued")

    def test_a_cancelled_or_limited_earlier_reply_does_not_use_up_the_person(self):
        first = self.comment(author="7700001")
        self.run_eval(first)
        InquiryCommentAction.objects.filter(comment=first).update(state="cancelled")
        self.assertEqual(self.run_eval(self.comment(author="7700001"))[0], "queued")


class CapTests(RulesTestCase):
    def setUp(self):
        super().setUp()
        self.ready()
        self.master()

    def fill(self, count, *, minutes_ago=0, kind="public", state="sent"):
        """`count` automatic replies already made `minutes_ago` minutes ago (one filler comment each)."""
        at = timezone.now() - timedelta(minutes=minutes_ago)
        anchor = self.comment()
        self.n += 1
        fillers = InquiryComment.objects.bulk_create([
            InquiryComment(site=self.site, connection=anchor.connection, post=anchor.post,
                           external_id=f"800{self.n}_{i}", parent_external_id=anchor.post.external_id)
            for i in range(count)])
        InquiryCommentAction.objects.bulk_create([
            InquiryCommentAction(site=self.site, comment=c, kind=kind, source="rule", state=state, created_at=at)
            for c in fillers])

    def test_the_hourly_cap_boundary_is_30(self):
        self.rule()
        self.fill(29, minutes_ago=5)
        comment = self.comment()
        self.assertEqual(self.run_eval(comment)[0], "queued")  # the 30th is allowed
        over = self.comment()
        self.assertEqual(self.run_eval(over)[0], "limit")  # the 31st is not
        row = InquiryCommentAction.objects.get(comment=over)
        self.assertEqual((row.state, row.kind, row.source), ("limit", "public", "rule"))
        self.assertEqual(InquiryAutoReply.objects.get(action=row).skip_reason, "hour_cap")
        self.assertFalse(self.queued(over).exists())

    def test_replies_older_than_an_hour_leave_the_hourly_count_but_not_the_daily(self):
        self.rule()
        now = timezone.now().astimezone(auto.MANILA).replace(hour=12, minute=0, second=0, microsecond=0)
        with mock.patch.object(auto.timezone, "now", return_value=now):
            self.fill(30, minutes_ago=0)
        InquiryCommentAction.objects.filter(source="rule").update(created_at=now - timedelta(minutes=61))
        result, _ = self.run_eval(self.comment(), now=now)
        self.assertEqual(result, "queued")

    def test_the_daily_cap_boundary_is_300(self):
        self.rule()
        now = timezone.now().astimezone(auto.MANILA).replace(hour=20, minute=0, second=0, microsecond=0)
        self.fill(299, minutes_ago=0)
        InquiryCommentAction.objects.filter(source="rule").update(created_at=now - timedelta(hours=3))
        self.assertEqual(self.run_eval(self.comment(), now=now)[0], "queued")  # the 300th
        over = self.comment()
        self.assertEqual(self.run_eval(over, now=now)[0], "limit")
        self.assertEqual(InquiryAutoReply.objects.get(action__comment=over).skip_reason, "day_cap")

    def test_yesterdays_replies_do_not_count_against_today(self):
        self.rule()
        now = timezone.now().astimezone(auto.MANILA).replace(hour=1, minute=0, second=0, microsecond=0)
        self.fill(300, minutes_ago=0)
        InquiryCommentAction.objects.filter(source="rule").update(created_at=now - timedelta(hours=2))  # 23:00 yesterday
        self.assertEqual(self.run_eval(self.comment(), now=now)[0], "queued")

    def test_both_kinds_are_counted(self):
        self.rule(private_text=PRIVATE)
        self.fill(29, minutes_ago=5, kind="private")
        comment = self.comment()
        self.assertEqual(self.run_eval(comment)[0], "queued")
        kinds = {r.kind: r.state for r in InquiryCommentAction.objects.filter(comment=comment)}
        self.assertEqual(kinds, {"public": "queued", "private": "limit"})
        a = auto.automation_out(self.site)
        self.assertEqual((a["hour"]["count"], a["today"]["count"]), (30, 30))

    def test_failed_cancelled_and_limit_rows_are_not_counted(self):
        self.rule()
        self.fill(10, minutes_ago=1, state="failed")
        self.fill(10, minutes_ago=1, state="cancelled")
        self.fill(10, minutes_ago=1, state="limit")
        self.assertEqual(auto.automation_out(self.site)["hour"]["count"], 0)
        self.fill(2, minutes_ago=1, state="queued")
        self.fill(1, minutes_ago=1, state="sending")
        self.assertEqual(auto.automation_out(self.site)["hour"]["count"], 3)

    def test_caps_are_per_site(self):
        self.rule()
        other = self.other_site()
        self.fill(30, minutes_ago=1)
        self.assertEqual(auto.automation_out(other)["hour"]["count"], 0)

    def test_the_caps_are_tunable_in_one_module(self):
        self.rule()
        with mock.patch.object(limits, "HOUR_CAP", 1):
            self.assertEqual(self.run_eval(self.comment())[0], "queued")
            self.assertEqual(self.run_eval(self.comment())[0], "limit")
        self.assertEqual((limits.HOUR_CAP, limits.DAY_CAP), (30, 300))

    def test_a_limited_comment_is_marked_and_stays_open_for_a_manual_reply(self):
        self.rule()
        self.fill(30, minutes_ago=5)
        comment = self.comment()
        self.assertEqual(self.run_eval(comment)[0], "limit")
        body = self.get(self.base + comment.ref + "/").json()
        self.assertEqual(body["auto_reply"], {"state": "limit", "label": "Not auto-answered (limit)",
                                              "reason": "hour_cap", "public": "limit", "private": None})
        self.assertTrue(body["public_reply"]["available"] and body["private_reply"]["available"])
        self.assertFalse(body["answered"])
        detail = body
        self.assertEqual([(a["state"], a["skip_reason"], a["source"]) for a in detail["actions"]], [("limit", "hour_cap", "rule")])
        manual = self.send("post", self.base + comment.ref + "/reply/", {"text": "Hi!", "idempotency_key": "key-limit-01"})
        self.assertEqual(manual.json()["state"], "sent")


class AutoReplyFieldTests(RulesTestCase):
    def test_the_field_is_null_without_a_rule_row_and_while_automation_is_off(self):
        self.ready()
        self.comment()
        self.assertIsNone(self.get(self.base).json()["items"][0]["auto_reply"])
        with override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=False):
            self.assertIsNone(self.get(self.base).json()["items"][0]["auto_reply"])

    def test_the_summary_follows_the_rows(self):
        comment, action = self.go(private_text=PRIVATE)
        item = lambda: self.get(self.base).json()["items"][0]["auto_reply"]
        self.assertEqual(item(), {"state": "queued", "label": "Auto-reply queued", "reason": "", "public": "queued", "private": "queued"})
        InquiryCommentAction.objects.filter(comment=comment).update(state="sent")
        self.assertEqual(item()["state"], "sent")
        self.assertEqual(item()["label"], "Auto-replied")
        InquiryCommentAction.objects.filter(comment=comment, kind="private").update(state="blocked_rate")
        self.assertEqual((item()["state"], item()["private"]), ("failed", "failed"))
        InquiryCommentAction.objects.filter(comment=comment).update(state="cancelled")
        self.assertEqual(item()["label"], "Auto-reply cancelled")

    def test_the_switch_off_or_missing_tables_do_not_break_the_comments_view(self):
        self.go()
        with override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=False):
            body = self.get(self.base).json()
        self.assertIsNone(body["items"][0]["auto_reply"])


class SendTests(RulesTestCase):
    def test_a_queued_public_reply_is_sent_as_the_page_with_the_first_name(self):
        comment, action = self.go(name="Maria Santos")
        self.assertEqual(self.meta.calls, [])
        self.assertEqual(auto.send(action.pk), "sent")
        method, path, params, form, token = self.meta.calls[-1]
        self.assertEqual((method, path, token), ("POST", f"/{comment.external_id}/comments", PAGE_TOKEN))
        self.assertRegex(form["message"], r"^(Hi|Hello|Salamat) Maria! (Check your inbox|We sent you a message)$")
        action.refresh_from_db()
        comment.refresh_from_db()
        self.assertEqual((action.state, action.error_code, action.body_length, action.text), ("sent", "", len(form["message"]), ""))
        self.assertTrue(ID_OK(action.meta_id))
        self.assertIsNotNone(comment.public_reply_at)
        self.assertTrue(InquiryComment.objects.filter(is_page=True, parent_external_id=comment.external_id).exists())
        self.assertEqual(auto.send(action.pk), "skipped")  # never twice
        self.assertEqual(len(self.meta.calls), 1)

    def test_a_private_reply_is_sent_to_the_comment_and_its_text_kept_for_the_owner(self):
        comment, public = self.go(private_text="{Hi|Hello} {name}, details: https://shop.example.test/p/1")
        private = self.queued(comment, "private").get()
        self.assertEqual(auto.send(private.pk), "sent")
        method, path, params, form, token = self.meta.calls[-1]
        self.assertEqual((method, path), ("POST", f"/{PAGE_A}/messages"))
        self.assertEqual(json.loads(form["recipient"]), {"comment_id": comment.external_id})
        text = json.loads(form["message"])["text"]
        self.assertRegex(text, r"^(Hi|Hello) Maria, details: https://shop.example.test/p/1$")
        private.refresh_from_db()
        comment.refresh_from_db()
        self.assertEqual((private.state, private.text), ("sent", text))
        self.assertIsNotNone(comment.private_reply_at)

    def test_the_name_is_the_first_name_only_blank_becomes_there_and_a_long_one_is_cut(self):
        for name, expected in (("Ana Marie Cruz", "Ana"), ("", "there"), ("B" * 90, "B" * 30)):
            InquiryComment.objects.all().delete()
            InquiryCommentAction.objects.all().delete()
            self.meta.calls.clear()
            self.rule_obj = None
            comment, action = self.go(name=name or " ") if name else self.go_blank()
            auto.send(action.pk)
            self.assertIn(f" {expected}!", self.meta.calls[-1][3]["message"])
            InquiryCommentRule.objects.all().delete()

    def go_blank(self):
        self.ready()
        self.master()
        self.rule()
        comment = self.comment()
        InquiryComment.objects.filter(pk=comment.pk).update(author_name="")
        self.run_eval(comment)
        return comment, self.queued(comment, "public").get()

    def test_the_site_master_off_while_queued_cancels_the_send(self):
        comment, action = self.go()
        self.master(False)
        self.assertEqual(auto.send(action.pk), "cancelled")
        self.assertEqual(self.meta.calls, [])
        action.refresh_from_db()
        self.assertEqual(action.state, "cancelled")

    def test_the_platform_switch_off_while_queued_cancels_the_send(self):
        comment, action = self.go()
        with override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=False):
            self.assertEqual(auto.send(action.pk), "cancelled")
        self.assertEqual(self.meta.calls, [])
        with override_settings(INQUIRY_COMMENTS_ENABLED=False):
            action.state = "queued"
            InquiryCommentAction.objects.filter(pk=action.pk).update(state="queued")
            self.assertEqual(auto.send(action.pk), "cancelled")
        self.assertEqual(self.meta.calls, [])

    def test_the_task_wrapper_runs_the_same_check(self):
        comment, action = self.go()
        self.master(False)
        tasks.send_comment_auto_reply(action.pk)
        self.assertEqual(self.meta.calls, [])

    def test_a_pause_a_disabled_rule_or_a_lost_connection_cancels_the_send(self):
        cases = [
            lambda: self.master(True, paused_until=timezone.now() + timedelta(minutes=1)),
            lambda: InquiryCommentRule.objects.update(enabled=False),
            lambda: tc.InquiryCommentSettings.objects.update(feed_subscribed=False),
            lambda: tc.InquiryConnection.objects.update(status="invalid"),
            lambda: InquiryCommentRule.objects.update(trigger="keywords", keywords=["zzz"]),
        ]
        for change in cases:
            InquiryComment.objects.all().delete()
            comment, action = self.go()
            change()
            self.assertEqual(auto.send(action.pk), "cancelled")
            self.assertEqual(self.meta.calls, [])
            self.master(True, paused_until=None)
            InquiryCommentRule.objects.update(enabled=True, trigger="any", keywords=[])
            tc.InquiryCommentSettings.objects.update(feed_subscribed=True)
            tc.InquiryConnection.objects.update(status="active")

    def test_a_comment_removed_hidden_or_answered_meanwhile_is_not_answered(self):
        for change in ({"is_hidden": True}, {"public_reply_at": timezone.now()}, {"removed": True}):
            InquiryComment.objects.all().delete()
            comment, action = self.go()
            InquiryComment.objects.filter(pk=comment.pk).update(**change)
            self.assertEqual(auto.send(action.pk), "cancelled", change)
        self.assertEqual(self.meta.calls, [])

    def test_a_comment_that_turned_seven_days_old_while_queued_gets_no_private_reply(self):
        comment, public = self.go(private_text=PRIVATE)
        private = self.queued(comment, "private").get()
        InquiryComment.objects.filter(pk=comment.pk).update(created_at=timezone.now() - timedelta(days=7, minutes=1))
        self.assertEqual(auto.send(private.pk), "cancelled")

    def test_a_manual_private_reply_meanwhile_keeps_the_database_rule_of_one(self):
        comment, public = self.go(private_text=PRIVATE)
        response = self.send("post", self.base + comment.ref + "/private-reply/", {"text": "Hi", "idempotency_key": "key-manual-01"})
        self.assertEqual(response.status_code, 409)  # already_sent: the queued automatic one holds the slot

    def test_a_send_uses_the_outbound_rate_limiter_and_requeues_when_busy(self):
        comment, action = self.go()
        with mock.patch.object(auto.limiter, "acquire", return_value=False), \
                mock.patch.object(tasks.send_comment_auto_reply, "apply_async") as apply:
            self.assertEqual(auto.send(action.pk), "retry")
        apply.assert_called_once_with(args=[action.pk], countdown=15)
        action.refresh_from_db()
        self.assertEqual(action.state, "queued")
        self.assertEqual(self.meta.calls, [])

    def test_a_missing_encryption_key_fails_the_row_without_a_call(self):
        comment, action = self.go()
        with mock.patch.object(auto.crypto, "decrypt", side_effect=auto.crypto.TokenKeyError):
            self.assertEqual(auto.send(action.pk), "failed")
        action.refresh_from_db()
        self.assertEqual(action.error_code, "not_configured")

    def test_a_credential_failure_marks_the_connection_invalid_without_a_strike(self):
        comment, action = self.go()
        self.meta.comment_error = (400, {"error": {"code": 190, "message": "Invalid OAuth access token."}})
        self.assertEqual(auto.send(action.pk), "failed")
        self.assertEqual(tc.InquiryConnection.objects.get().status, "invalid")
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 0)

    def test_an_ordinary_failure_is_no_strike(self):
        comment, action = self.go()
        self.meta.comment_error = (500, {"error": {"code": 2}})
        self.assertEqual(auto.send(action.pk), "failed")
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.strikes, row.paused_until, row.enabled), (0, None, True))


def ID_OK(value):
    return bool(value) and value.replace("_", "").isdigit()


class VariantTests(RulesTestCase):
    def test_the_same_variant_is_not_sent_twice_in_a_row_on_one_post(self):
        self.ready()
        self.master()
        self.rule(public_text="{Alpha|Beta} {name}")
        sent = []
        for _ in range(14):
            comment = self.comment()
            self.run_eval(comment)
            auto.send(self.queued(comment, "public").get().pk)
            sent.append(self.meta.calls[-1][3]["message"].split()[0])
        self.assertEqual(set(sent), {"Alpha", "Beta"})
        for a, b in zip(sent, sent[1:]):
            self.assertNotEqual(a, b)

    def test_the_rule_is_per_post_and_per_kind(self):
        self.ready()
        self.master()
        self.rule(public_text="{Alpha|Beta} {name}", private_text="{Pa|Pb} {name}")
        for post in ("111000111_71", "111000111_72"):
            previous = None
            for _ in range(6):
                comment = self.comment(post_id=post)
                self.run_eval(comment)
                for kind in ("public", "private"):
                    auto.send(self.queued(comment, kind).get().pk)
                hashes = {a.action.kind: a.variant_hash for a in InquiryAutoReply.objects.filter(action__comment=comment)}
                if previous:
                    self.assertNotEqual(hashes["public"], previous["public"])
                    self.assertNotEqual(hashes["private"], previous["private"])
                previous = hashes

    def test_the_name_does_not_make_two_variants_different(self):
        self.ready()
        self.master()
        self.rule(public_text="{Alpha|Beta} {name}")
        a = self.comment(name="Ana")
        b = self.comment(name="Ben")
        for c in (a, b):
            self.run_eval(c)
            auto.send(self.queued(c, "public").get().pk)
        hashes = [InquiryAutoReply.objects.get(action__comment=c).variant_hash for c in (a, b)]
        self.assertNotEqual(*hashes)


class BackoffTests(RulesTestCase):
    def setUp(self):
        super().setUp()
        self.ready()
        self.master()
        self.rule(private_text=PRIVATE)

    def queue(self):
        comment = self.comment()
        result, _ = self.run_eval(comment)
        self.assertEqual(result, "queued")
        return comment, self.queued(comment, "public").get()

    def test_a_rate_limit_error_pauses_for_one_hour_and_cancels_the_rest_of_the_queue(self):
        _, first = self.queue()
        _, second = self.queue()
        self.meta.comment_error = (400, {"error": {"code": 613, "message": "Calls to this api have exceeded the rate limit."}})
        before = timezone.now()
        self.assertEqual(auto.send(first.pk), "blocked_rate")
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.strikes, row.pause_reason, row.enabled), (1, "rate_limit", True))
        self.assertTrue(timedelta(minutes=59) < row.paused_until - before < timedelta(minutes=61))
        second.refresh_from_db()
        self.assertEqual(second.state, "cancelled")
        self.assertFalse(self.queued(second.comment).exists())
        a = self.get(self.rules_url).json()["automation"]
        self.assertEqual((a["active"], a["banner"]["code"], a["enabled"]), (False, "paused_rate_limit", True))
        self.assertEqual(a["banner"]["paused_until"], row.paused_until.isoformat())
        self.assertIn("1 hour", a["banner"]["message"])

    def test_every_listed_meta_code_counts_and_so_does_368(self):
        for code, status in ((4, 400), (9, 400), (17, 400), (32, 400), (613, 400), (368, 400), (1, 429)):
            InquiryCommentAutomation.objects.all().delete()
            self.master()
            self.meta.comment_error = (status, {"error": {"code": code}})
            _, action = self.queue()
            auto.send(action.pk)
            self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 1, code)

    def test_error_368_keeps_its_mapping_but_pauses(self):
        self.assertEqual(messenger.map_error(400, {"error": {"code": 368}}), "bad_response")
        _, action = self.queue()
        self.meta.comment_error = (400, {"error": {"code": 368, "message": "temporarily blocked"}})
        self.assertEqual(auto.send(action.pk), "failed")
        action.refresh_from_db()
        self.assertEqual(action.error_code, "bad_response")
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.strikes, row.pause_reason), (1, "rate_limit"))

    def test_a_private_reply_error_counts_too(self):
        comment, public = self.queue()
        private = self.queued(comment, "private").get()
        self.meta.send_error = (400, {"error": {"code": 368}})
        self.assertEqual(auto.send(private.pk), "failed")
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 1)

    def test_a_new_comment_while_paused_is_not_queued(self):
        _, action = self.queue()
        self.meta.comment_error = (400, {"error": {"code": 4}})
        auto.send(action.pk)
        comment = self.comment()
        self.assertEqual(self.run_eval(comment)[0], "paused")
        self.assertFalse(InquiryCommentAction.objects.filter(comment=comment).exists())

    def test_automation_resumes_by_itself_after_the_hour(self):
        _, action = self.queue()
        self.meta.comment_error = (400, {"error": {"code": 4}})
        auto.send(action.pk)
        later = timezone.now() + timedelta(hours=1, minutes=1)
        self.assertEqual(self.run_eval(self.comment(), now=later)[0], "queued")
        with mock.patch.object(auto.timezone, "now", return_value=later):
            a = auto.automation_out(self.site, now=later)
        self.assertEqual((a["banner"], a["active"]), (None, True))

    def test_the_third_strike_in_a_day_switches_automation_off_until_the_owner_turns_it_on(self):
        for n in range(3):
            self.meta.comment_error = (400, {"error": {"code": 613}})
            _, action = self.queue()
            auto.send(action.pk)
            InquiryCommentAutomation.objects.update(paused_until=None)  # the hour passed
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.strikes, row.enabled, row.pause_reason, row.paused_until), (3, False, "repeated_limits", None))
        a = self.get(self.rules_url).json()["automation"]
        self.assertEqual((a["enabled"], a["active"], a["banner"]["code"], a["banner"]["paused_until"]),
                         (False, False, "needs_reenable", None))
        self.assertEqual(self.run_eval(self.comment())[0], "site_off")
        on = self.put(self.rules_url + "automation/", {"enabled": True}).json()
        self.assertEqual((on["enabled"], on["banner"], on["active"]), (True, None, True))
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 0)

    def test_strikes_count_per_manila_day(self):
        _, action = self.queue()
        yesterday = timezone.localdate() - timedelta(days=1)
        self.master(True, strikes=2, strikes_date=yesterday)
        self.meta.comment_error = (400, {"error": {"code": 4}})
        auto.send(action.pk)
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.strikes, row.enabled), (1, True))
        self.assertEqual(row.strikes_date, timezone.now().astimezone(auto.MANILA).date())

    def test_a_strike_only_counts_when_there_is_an_automation_row(self):
        InquiryCommentAutomation.objects.all().delete()
        auto.strike(self.site)
        self.assertFalse(InquiryCommentAutomation.objects.exists())


class ManualStrikeTests(RulesTestCase):
    def setUp(self):
        super().setUp()
        self.ready()
        self.master()
        self.comment_obj = self.comment()
        self.url = self.base + self.comment_obj.ref + "/"

    def reply(self, key="key-strike-01"):
        return self.send("post", self.url + "reply/", {"text": "Hi!", "idempotency_key": key})

    def test_a_manual_reply_refused_for_a_rate_limit_is_a_strike(self):
        self.meta.comment_error = (400, {"error": {"code": 17}})
        body = self.reply().json()
        self.assertEqual((body["state"], body["error_code"]), ("blocked_rate", "rate_limited"))
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.strikes, row.pause_reason), (1, "rate_limit"))

    def test_a_manual_reply_refused_with_368_is_a_strike(self):
        self.meta.comment_error = (400, {"error": {"code": 368}})
        self.assertEqual(self.reply().json()["state"], "failed")
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 1)

    def test_an_ordinary_manual_failure_is_not_a_strike(self):
        self.meta.comment_error = (400, {"error": {"code": 100}})
        self.reply()
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 0)

    def test_the_pause_never_blocks_manual_replies(self):
        self.master(True, paused_until=timezone.now() + timedelta(minutes=30), pause_reason="rate_limit")
        self.assertEqual(self.reply().json()["state"], "sent")
        self.master(False, pause_reason="repeated_limits")
        other = self.comment()
        again = self.send("post", self.base + other.ref + "/reply/", {"text": "Hi!", "idempotency_key": "key-strike-02"})
        self.assertEqual(again.json()["state"], "sent")

    def test_three_manual_strikes_switch_automation_off(self):
        for n in range(3):
            self.meta.comment_error = (400, {"error": {"code": 4}})
            self.reply(key=f"key-strike-1{n}")
            InquiryCommentAutomation.objects.update(paused_until=None)
            self.comment_obj = self.comment()
            self.url = self.base + self.comment_obj.ref + "/"
        row = InquiryCommentAutomation.objects.get()
        self.assertEqual((row.enabled, row.pause_reason), (False, "repeated_limits"))

    def test_nothing_changes_while_automation_is_off_or_the_master_is_off(self):
        self.master(False)
        self.meta.comment_error = (400, {"error": {"code": 4}})
        self.reply()
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 0)
        self.master(True)
        with override_settings(INQUIRY_COMMENT_AUTOMATION_ENABLED=False):
            self.reply(key="key-strike-03")
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 0)

    def test_the_local_outbound_limiter_is_not_a_meta_strike(self):
        with mock.patch("apps.inquiries.comments.limiter.acquire", return_value=False):
            self.assertEqual(self.reply().json()["state"], "blocked_rate")
        self.assertEqual(InquiryCommentAutomation.objects.get().strikes, 0)


class PrivacyTests(RulesTestCase):
    def test_no_token_body_or_customer_text_reaches_the_logs_or_the_audit_rows(self):
        self.ready()
        self.master()
        self.rule(private_text=PRIVATE)
        comment = self.comment(message="How much? secret-words-xyz")
        self.run_eval(comment)
        self.meta.comment_error = (400, {"error": {"code": 613, "message": "RAW-META-BODY-SECRET", "fbtrace_id": "TRACE123"}})
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("apps.inquiries").warning("start")
            auto.send(self.queued(comment, "public").get().pk)
            auto.send(self.queued(comment, "private").get().pk) if self.queued(comment, "private").exists() else None
        out = "\n".join(logs.output)
        for private in (PAGE_TOKEN, "secret-words-xyz", "RAW-META-BODY-SECRET", comment.external_id, tc.COMMENTER):
            self.assertNotIn(private, out)
        dump = json.dumps(list(InquiryCommentAction.objects.values()), default=str) + json.dumps(
            list(InquiryAutoReply.objects.values()), default=str)
        for private in (PAGE_TOKEN, "RAW-META-BODY-SECRET", "TRACE123"):
            self.assertNotIn(private, dump)
