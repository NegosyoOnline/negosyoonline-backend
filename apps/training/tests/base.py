"""Content helpers shared by the training tests."""
from apps.training.models import Course, Lesson, Module

YT = "https://www.youtube.com/embed/dQw4w9WgXcQ"


def make_course(title="Ways to Earn", **fields):
    values = {"slug": title.lower().replace(" ", "-"), **fields}
    return Course.objects.create(title=title, **values)


def make_module(course, title="Module", **fields):
    return Module.objects.create(course=course, title=title, **fields)


def make_lesson(module, title="Lesson", **fields):
    return Lesson.objects.create(module=module, title=title, video_url=fields.pop("video_url", YT), **fields)


def make_course_with_lessons(counts=(2, 2), title="Ways to Earn"):
    """A course whose modules hold `counts` lessons: L1.1, L1.2, L2.1 ... in order."""
    course = make_course(title)
    lessons = []
    for m, count in enumerate(counts, start=1):
        module = make_module(course, f"Module {m}", order=m)
        for n in range(1, count + 1):
            lessons.append(make_lesson(module, f"L{m}.{n}", order=n))
    return course, lessons
