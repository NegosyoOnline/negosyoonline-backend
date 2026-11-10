"""Site templates. A template is the capability source of a site. The MVP has one."""
SANTE = "sante"
TEMPLATES = {SANTE: "Santé"}
CHOICES = list(TEMPLATES.items())


def offered():
    """The templates a subscriber may choose when creating a site."""
    return [{"id": key, "label": label} for key, label in TEMPLATES.items()]
