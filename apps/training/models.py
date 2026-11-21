"""Member training: courses, modules, lessons, each member's progress, and live trainings.

Port of TWCinc app/university/models.py (Course, Module, Lesson, LessonProgress), trimmed: no
access tier, author, certificates, comments or CourseProgress table (percent is computed in
services.py), no streaming bootcamp (LiveTraining is a title, a time and a join link). Content
belongs to one Subscriber's site and is written by that owner in the dash. Write these tables
through services.py.
"""
from django.db import models
from django.utils import timezone

from apps.common.ids import new_public_id


def new_course_id():
    return new_public_id("crs")


def new_module_id():
    return new_public_id("mdl")


def new_lesson_id():
    return new_public_id("lsn")


def new_live_id():
    return new_public_id("liv")


class Course(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_course_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.CASCADE, related_name="courses")
    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    description = models.TextField(blank=True)
    image_key = models.CharField(max_length=200, blank=True, default="")
    order = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    # Only the content source store may set this: the course is then offered to other stores to
    # copy. copied_from is the public id of the source course this one was copied from, a plain
    # string and never a foreign key across stores.
    shared = models.BooleanField(default=False)
    copied_from = models.CharField(max_length=40, blank=True, default="")
    # The timestamp is the flag (TWCinc spec 2026-08-08): archiving also switches the course off.
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "slug"], name="training_course_slug"),
            models.CheckConstraint(
                condition=~models.Q(active=True, archived_at__isnull=False),
                name="training_course_archived_inactive",
            ),
        ]

    def __str__(self):
        return self.public_id


class Module(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_module_id, editable=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=120)
    order = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return self.public_id


class Lesson(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_lesson_id, editable=False)
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=160)
    duration = models.CharField(max_length=20, blank=True)  # shown as typed, e.g. "6:30"
    description = models.TextField(blank=True)
    video_url = models.CharField(max_length=500, blank=True)  # an allowlisted embed URL (services.py)
    image_key = models.CharField(max_length=200, blank=True, default="")
    # True on a copy whose Bunny video was not copied: the dash shows "Add your video".
    needs_video = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return self.public_id


class LessonProgress(models.Model):
    """One row per member and lesson. A row with no completed_at is a lesson that was opened."""

    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, related_name="lesson_progress")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress")
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["member", "lesson"], name="training_progress_once")]

    def __str__(self):
        return f"{self.member_id}:{self.lesson_id}"


class LiveTraining(models.Model):
    """A scheduled live session, for the members of its site only."""

    public_id = models.CharField(max_length=40, unique=True, default=new_live_id, editable=False)
    site = models.ForeignKey("sites.Site", on_delete=models.CASCADE, related_name="live_trainings")
    title = models.CharField(max_length=120)
    starts_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    description = models.TextField(blank=True)
    join_url = models.CharField(max_length=500, blank=True)
    thumbnail_key = models.CharField(max_length=200, blank=True, default="")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "pk"]
        indexes = [models.Index(fields=["site", "active", "starts_at"], name="training_live_next")]

    def __str__(self):
        return self.public_id
