from django import forms
from .models import Rating


class RatingForm(forms.ModelForm):
    class Meta:
        model = Rating
        fields = ["rate", "review"]
        labels = {"rate": "Rate between 1-5", "review": "Write your review"}
        widgets = {
            "review": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Share your thoughts on this drink..."}
            ),
        }
