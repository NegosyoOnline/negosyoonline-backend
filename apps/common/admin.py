"""Shared pieces for admin lists that show people's contact details masked."""
from django import forms
from django.contrib import admin
from django.contrib.admin import helpers
from django.utils.safestring import mark_safe


class MaskedListAdmin(admin.ModelAdmin):
    """A list whose row checkbox does not repeat the record's name.

    Django labels each row's checkbox with str(record), which for these records holds a full
    email address. The label names the record by its number instead.
    """

    @admin.display(description=mark_safe('<input type="checkbox" id="action-toggle">'))
    def action_checkbox(self, obj):
        attrs = {
            "class": "action-select",
            "aria-label": f"Select {self.opts.verbose_name} {obj.pk} for an action",
        }
        checkbox = forms.CheckboxInput(attrs, lambda value: False)
        return checkbox.render(helpers.ACTION_CHECKBOX_NAME, str(obj.pk))
