"""The member's training routes: sequential unlock, percent, access and isolation."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.members.models import MemberNotification
from apps.members.tests.base import NOT_FOUND, NOT_SIGNED_IN, OTHER_HOST, MembersTestCase
from apps.training import services
from apps.training.models import LessonProgress, LiveTraining
from apps.training.tests.base import make_course, make_course_with_lessons, make_lesson, make_module

PREFIX = "training/"
BEN = {"email": "ben@buyer.test", "mobile": "09175550120", "name": "Ben"}
CARLA = {"email": "carla@buyer.test", "mobile": "09175550121", "name": "Carla"}


class TrainingTestCase(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in()

    def call(self, path, token=None, **extra):
        return self.post(PREFIX + path, token=self.token if token is None else token, **extra)

    def complete(self, lesson):
        """Completion now comes only from watching a Stream video: these sequence tests write the
        record directly (test_watch.py covers the watch post and the refused manual route)."""
        from django.utils import timezone

        row, _ = LessonProgress.objects.get_or_create(member=self.member, lesson=lesson)
        if row.completed_at is None:
            row.completed_at = timezone.now()
            row.save(update_fields=["completed_at"])


class SequentialUnlockTests(TrainingTestCase):
    def setUp(self):
        super().setUp()
        self.course, self.lessons = make_course_with_lessons(self.site, (2, 2))

    def test_only_the_first_lesson_is_open_at_the_start(self):
        detail = self.call(f"courses/{self.course.public_id}/").json()
        flags = [(x["title"], x["locked"]) for m in detail["modules"] for x in m["lessons"]]
        self.assertEqual(flags, [("L1.1", False), ("L1.2", True), ("L2.1", True), ("L2.2", True)])
        self.assertEqual(detail["next_lesson"]["title"], "L1.1")

    def test_a_locked_lesson_is_refused_and_cannot_be_completed(self):
        second = self.lessons[1]
        response = self.call(f"lessons/{second.public_id}/")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "lesson_locked")
        self.assertEqual(self.call(f"lessons/{second.public_id}/complete/").status_code, 403)
        self.assertNotIn(b"youtube", self.call(f"lessons/{second.public_id}/").content)
        self.assertFalse(LessonProgress.objects.filter(lesson=second).exists())

    def test_completing_a_lesson_opens_the_next_one_across_modules(self):
        for lesson in self.lessons[:2]:
            self.complete(lesson)
        self.assertEqual(self.call(f"lessons/{self.lessons[2].public_id}/").status_code, 200)
        self.assertEqual(self.call(f"lessons/{self.lessons[3].public_id}/").status_code, 409)

    def test_a_switched_off_lesson_is_skipped_by_the_sequence(self):
        self.lessons[1].active = False
        self.lessons[1].save()
        self.complete(self.lessons[0])
        self.assertEqual(self.call(f"lessons/{self.lessons[2].public_id}/").status_code, 200)
        self.assertEqual(self.call(f"lessons/{self.lessons[1].public_id}/").status_code, 404)

    def test_opening_a_lesson_records_the_start_and_answers_navigation(self):
        self.complete(self.lessons[0])
        body = self.call(f"lessons/{self.lessons[1].public_id}/").json()
        row = LessonProgress.objects.get(lesson=self.lessons[1])
        self.assertIsNotNone(row.started_at)
        self.assertIsNone(row.completed_at)
        self.assertEqual(body["previous_lesson"]["title"], "L1.1")
        self.assertEqual(body["next_lesson"]["title"], "L2.1")
        self.assertTrue(body["next_lesson_locked"])
        self.assertEqual(body["next_module"]["title"], "Module 2")
        self.assertEqual(body["next_module"]["first_lesson_id"], self.lessons[2].public_id)
        self.assertIsNone(body["previous_module"])
        self.assertEqual([x["title"] for x in body["lessons"]], ["L1.1", "L1.2"])
        self.assertEqual(body["video_url"], "https://www.youtube.com/embed/dQw4w9WgXcQ")
        self.assertEqual(body["module"]["title"], "Module 1")

    def test_progress_is_the_members_own(self):
        self.new_member(self.site, email=CARLA["email"], mobile=CARLA["mobile"], name="Carla")
        self.complete(self.lessons[0])
        carla = self.sign_in(CARLA["email"])
        detail = self.call(f"courses/{self.course.public_id}/", token=carla).json()
        self.assertEqual(detail["completed_lessons"], 0)
        self.assertEqual(self.call(f"lessons/{self.lessons[1].public_id}/", token=carla).status_code, 409)


class ProgressTests(TrainingTestCase):
    def first_row(self):
        return self.call("courses/").json()["courses"][0]

    def test_percent_is_computed_from_the_active_lessons(self):
        course, lessons = make_course_with_lessons(self.site, (3,))
        row = self.first_row()
        self.assertEqual((row["percent"], row["total_lessons"]), (0, 3))
        self.complete(lessons[0])
        row = self.first_row()
        self.assertEqual((row["percent"], row["completed_lessons"]), (33, 1))
        self.complete(lessons[1])
        self.assertEqual(self.first_row()["percent"], 66)
        self.complete(lessons[2])
        row = self.first_row()
        self.assertEqual((row["percent"], row["completed_lessons"]), (100, 3))
        lessons[2].active = False  # one fewer lesson to do: 2 of 2
        lessons[2].save()
        row = self.first_row()
        self.assertEqual((row["percent"], row["total_lessons"]), (100, 2))

    def test_module_counts_are_n_of_m(self):
        course, lessons = make_course_with_lessons(self.site, (2, 3))
        self.complete(lessons[0])
        modules = self.call(f"courses/{course.public_id}/").json()["modules"]
        self.assertEqual([(m["completed_lessons"], m["total_lessons"]) for m in modules], [(1, 2), (0, 3)])

    def test_module_count_and_module_number_count_only_visible_modules(self):
        course, lessons = make_course_with_lessons(self.site, (1, 1))
        make_module(course, "Empty middle", order=15)  # no lesson: hidden, takes no number
        third = make_module(course, "Module 3", order=30)
        last = make_lesson(third, "L3.1", order=1)
        make_lesson(make_module(course, "Off", order=40, active=False), "Hidden")
        make_course(self.site, "Soon", order=5)
        rows = {c["title"]: c for c in self.call("courses/").json()["courses"]}
        self.assertEqual((rows["Ways to Earn"]["module_count"], rows["Soon"]["module_count"]), (3, 0))
        detail = self.call(f"courses/{course.public_id}/").json()
        self.assertEqual(detail["module_count"], 3)
        self.assertEqual([m["module_number"] for m in detail["modules"]], [1, 2, 3])
        self.complete(lessons[0])
        self.complete(lessons[1])
        body = self.call(f"lessons/{last.public_id}/").json()
        self.assertEqual(body["module"]["module_number"], 3)
        self.assertEqual(body["previous_module"]["module_number"], 2)
        self.assertEqual(body["previous_module"]["title"], "Module 2")
        self.assertIsNone(body["next_module"])
        middle = self.call(f"lessons/{lessons[1].public_id}/").json()
        self.assertEqual(middle["module"]["module_number"], 2)
        self.assertEqual(middle["previous_module"]["module_number"], 1)
        self.assertEqual(middle["next_module"]["module_number"], 3)

    def test_a_course_with_no_active_lesson_is_coming_soon_and_an_empty_module_is_hidden(self):
        soon = make_course(self.site, "Product Trainings", order=2)
        make_module(soon, "Empty")
        course, _ = make_course_with_lessons(self.site, (1,))
        make_module(course, "Hidden", order=9)
        rows = {c["title"]: c for c in self.call("courses/").json()["courses"]}
        self.assertTrue(rows["Product Trainings"]["coming_soon"])
        self.assertEqual(rows["Product Trainings"]["percent"], 0)
        self.assertFalse(rows["Ways to Earn"]["coming_soon"])
        detail = self.call(f"courses/{course.public_id}/").json()
        self.assertEqual([m["title"] for m in detail["modules"]], ["Module 1"])

    def test_inactive_and_archived_courses_are_not_listed_and_not_found(self):
        off = make_course(self.site, "Off", active=False)
        old = make_course(self.site, "Old", active=False, archived_at=timezone.now())
        shown = make_course(self.site, "Shown")
        found = [c["id"] for c in self.call("courses/").json()["courses"]]
        self.assertEqual(found, [shown.public_id])
        for course in (off, old):
            response = self.call(f"courses/{course.public_id}/")
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))

    def test_courses_come_in_sequence_order(self):
        b, a = make_course(self.site, "B", order=2), make_course(self.site, "A", order=1)
        self.assertEqual([c["id"] for c in self.call("courses/").json()["courses"]], [a.public_id, b.public_id])

    def test_resume_points_at_the_first_unfinished_lesson_of_the_course_touched_last(self):
        self.assertIsNone(self.call("courses/").json()["resume"])
        course, lessons = make_course_with_lessons(self.site, (2,))
        self.assertIsNone(self.call("courses/").json()["resume"])
        self.complete(lessons[0])
        resume = self.call("courses/").json()["resume"]
        self.assertEqual((resume["id"], resume["title"]), (lessons[1].public_id, "L1.2"))
        self.assertEqual(resume["course"]["id"], course.public_id)
        self.complete(lessons[1])
        self.assertIsNone(self.call("courses/").json()["resume"])  # finished: nothing to pick up


class AccessAndIsolationTests(TrainingTestCase):
    def setUp(self):
        super().setUp()
        self.course, self.lessons = make_course_with_lessons(self.site, (1,))

    def paths(self):
        return [
            "courses/", f"courses/{self.course.public_id}/", f"lessons/{self.lessons[0].public_id}/",
            f"lessons/{self.lessons[0].public_id}/complete/", "live/",
        ]

    def test_a_session_is_needed_on_every_route(self):
        for path in self.paths():
            with self.subTest(path=path):
                for token in ("", "not-a-real-token-0123456789"):
                    response = self.post(PREFIX + path, token=token)
                    self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN))
        self.assertFalse(LessonProgress.objects.exists())

    def test_the_credential_the_switch_and_an_unknown_host_are_checked(self):
        self.assertEqual(self.post(PREFIX + "courses/", secret=None, token=self.token).status_code, 401)
        with override_settings(MEMBERS_ENABLED=False):
            response = self.call("courses/")
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(self.call("courses/", host="nobody.negosyotest.com").json(), NOT_FOUND)

    def test_a_session_of_another_site_does_not_open_this_host(self):
        self.new_member(self.other_site, **{"email": BEN["email"], "mobile": BEN["mobile"], "name": "Ben"})
        ben = self.sign_in(BEN["email"], host=OTHER_HOST)
        self.assertEqual(self.call("courses/", token=ben).status_code, 401)

    def test_a_course_of_another_site_is_never_found(self):
        elsewhere = make_course(self.other_site, "Elsewhere")
        lesson = make_lesson(make_module(elsewhere))
        response = self.call(f"courses/{elsewhere.public_id}/")
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(self.call(f"lessons/{lesson.public_id}/").status_code, 404)
        self.assertEqual(self.call(f"lessons/{lesson.public_id}/complete/").status_code, 404)
        self.assertNotIn("Elsewhere", [c["title"] for c in self.call("courses/").json()["courses"]])
        LiveTraining.objects.create(site=self.other_site, title="Other", starts_at=timezone.now() + timedelta(days=1))
        self.assertIsNone(self.call("live/").json()["live"])
        self.assertFalse(LessonProgress.objects.exists())

    def test_a_second_site_sees_only_its_own_courses_and_never_the_first_ones(self):
        mine = make_course(self.other_site, "Bens course")
        self.new_member(self.other_site, email=BEN["email"], mobile=BEN["mobile"], name="Ben")
        ben = self.sign_in(BEN["email"], host=OTHER_HOST)
        theirs = [c["id"] for c in self.call("courses/", token=ben, host=OTHER_HOST).json()["courses"]]
        self.assertEqual(theirs, [mine.public_id])
        ours = [c["id"] for c in self.call("courses/").json()["courses"]]
        self.assertEqual(ours, [self.course.public_id])
        # the first site's ids are 404 on the second site's host
        for path in (f"courses/{self.course.public_id}/", f"lessons/{self.lessons[0].public_id}/"):
            self.assertEqual(self.call(path, token=ben, host=OTHER_HOST).json(), NOT_FOUND)

    def test_the_same_slug_may_exist_on_two_sites(self):
        make_course(self.other_site, "Ways to Earn")  # the same slug as self.course

    def test_a_site_of_another_template_gets_nothing(self):
        self.site.template = "basic"
        self.site.save()
        for path in self.paths():
            self.assertEqual(self.call(path).json(), NOT_FOUND, path)

    def test_the_answers_carry_no_flag_or_row_number(self):
        body = self.call(f"lessons/{self.lessons[0].public_id}/").json()
        self.assertNotIn("pk", body)
        self.assertNotIn(b'"active"', self.call(f"courses/{self.course.public_id}/").content)


class LiveTests(TrainingTestCase):
    def live(self, **fields):
        values = {"site": self.site, "title": "Bootcamp", "starts_at": timezone.now() + timedelta(days=2)}
        return LiveTraining.objects.create(**{**values, **fields})

    def next(self, token=None):
        host = {"host": OTHER_HOST} if token else {}
        return self.call("live/", token=token, **host).json()["live"]

    def test_none_when_nothing_is_scheduled(self):
        self.assertIsNone(self.next())

    def test_the_soonest_upcoming_active_one_is_returned(self):
        now = timezone.now()
        self.live(title="Later", starts_at=now + timedelta(days=5))
        sooner = self.live(title="Sooner", starts_at=now + timedelta(days=1), join_url="https://zoom.example/j/1")
        self.live(title="Off", starts_at=now + timedelta(hours=1), active=False)
        self.live(title="Past", starts_at=now - timedelta(days=1), duration_minutes=60)
        found = self.next()
        self.assertEqual((found["id"], found["title"]), (sooner.public_id, "Sooner"))
        self.assertEqual(found["join_url"], "https://zoom.example/j/1")
        self.assertEqual(
            set(found),
            {"id", "title", "starts_at", "duration_minutes", "description", "join_url", "thumbnail_url"},
        )

    def test_one_in_progress_still_counts(self):
        running = self.live(starts_at=timezone.now() - timedelta(minutes=10), duration_minutes=60)
        self.assertEqual(self.next()["id"], running.public_id)

    def test_a_live_is_seen_only_by_its_own_sites_members(self):
        self.live(title="Bens own", site=self.other_site, starts_at=timezone.now() + timedelta(hours=1))
        mine = self.live(title="Ours", site=self.site, starts_at=timezone.now() + timedelta(days=3))
        self.assertEqual(self.next()["id"], mine.public_id)
        self.assertNotIn(b"Bens own", self.call("live/").content)
        self.new_member(self.other_site, email=BEN["email"], mobile=BEN["mobile"], name="Ben")
        ben = self.sign_in(BEN["email"], host=OTHER_HOST)
        self.assertEqual(self.next(ben)["title"], "Bens own")

    def test_a_second_site_with_no_live_sees_none(self):
        self.live()
        self.new_member(self.other_site, email=BEN["email"], mobile=BEN["mobile"], name="Ben")
        ben = self.sign_in(BEN["email"], host=OTHER_HOST)
        self.assertIsNone(self.next(ben))


class LiveBellTests(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.ben = self.new_member(self.other_site, email=BEN["email"], mobile=BEN["mobile"], name="Ben")
        self.carla = self.new_member(self.site, email=CARLA["email"], mobile=CARLA["mobile"], name="Carla")
        self.when = timezone.now() + timedelta(days=3)

    def bells(self, member):
        return list(MemberNotification.objects.filter(member=member))

    def test_a_live_notifies_its_own_sites_members_only(self):
        services.create_live(self.site, {"title": "Ours", "starts_at": self.when})
        for member in (self.member, self.carla):
            (row,) = self.bells(member)
            self.assertEqual(
                (row.kind, row.title, row.url, row.site_id),
                ("live", "Live training: Ours", "/dashboard/training/", self.site.pk),
            )
        self.assertEqual(self.bells(self.ben), [])

    def test_an_inactive_live_or_an_update_notifies_nobody(self):
        services.create_live(self.site, {"title": "Off", "starts_at": self.when, "active": False})
        live = services.create_live(self.other_site, {"title": "On", "starts_at": self.when})
        before = MemberNotification.objects.count()
        services.update_live(self.other_site, live.public_id, {"title": "Renamed", "active": False})
        services.update_live(self.other_site, live.public_id, {"active": True})
        self.assertEqual(MemberNotification.objects.count(), before)
        self.assertEqual(before, 1)  # only Ben, for "On"
