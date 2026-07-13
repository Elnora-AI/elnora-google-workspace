"""Unit tests for forms.py — spec translation and ID extraction (no API calls)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).parent.parent / "lib"
sys.path.insert(0, str(LIB_DIR))

import forms as forms_lib  # noqa: E402
from output import ValidationError  # noqa: E402


# ---------- _extract_form_id ----------


class TestExtractFormId:
    def test_bare_id_returns_unchanged(self):
        fid = "1ExampleFormId0123456789abcdefGHIJKLMNOP"
        assert forms_lib._extract_form_id(fid) == fid

    def test_editor_url_extracts_id(self):
        url = "https://docs.google.com/forms/d/1ExampleFormId_-Cw/edit"
        assert forms_lib._extract_form_id(url) == "1ExampleFormId_-Cw"

    def test_responder_url_rejected(self):
        with pytest.raises(ValidationError, match="responder URL"):
            forms_lib._extract_form_id(
                "https://docs.google.com/forms/d/e/1FAIpQLSd2/viewform"
            )


# ---------- _item_spec_to_api ----------


class TestItemSpecToApi:
    def test_section_basic(self):
        out = forms_lib._item_spec_to_api(
            {"type": "section", "title": "About you"}
        )
        assert out == {"title": "About you", "pageBreakItem": {}}

    def test_section_with_description(self):
        out = forms_lib._item_spec_to_api(
            {"type": "section", "title": "About you", "description": "Quick intro"}
        )
        assert out["pageBreakItem"] == {}
        assert out["description"] == "Quick intro"

    def test_text_short(self):
        out = forms_lib._item_spec_to_api({"type": "text", "title": "Name"})
        assert out["title"] == "Name"
        assert out["questionItem"]["question"]["textQuestion"] == {"paragraph": False}
        assert out["questionItem"]["question"]["required"] is False

    def test_text_paragraph_via_alias(self):
        out = forms_lib._item_spec_to_api({"type": "paragraph", "title": "Tell us"})
        assert out["questionItem"]["question"]["textQuestion"] == {"paragraph": True}

    def test_text_paragraph_via_flag(self):
        out = forms_lib._item_spec_to_api(
            {"type": "text", "title": "Tell us", "paragraph": True}
        )
        assert out["questionItem"]["question"]["textQuestion"] == {"paragraph": True}

    def test_text_required_propagates(self):
        out = forms_lib._item_spec_to_api(
            {"type": "text", "title": "Name", "required": True}
        )
        assert out["questionItem"]["question"]["required"] is True

    def test_linear_scale_full(self):
        out = forms_lib._item_spec_to_api(
            {
                "type": "linear_scale",
                "title": "Rate it",
                "low": 1,
                "high": 10,
                "low_label": "Bad",
                "high_label": "Great",
                "required": True,
            }
        )
        scale = out["questionItem"]["question"]["scaleQuestion"]
        assert scale == {"low": 1, "high": 10, "lowLabel": "Bad", "highLabel": "Great"}
        assert out["questionItem"]["question"]["required"] is True

    def test_linear_scale_defaults(self):
        out = forms_lib._item_spec_to_api(
            {"type": "linear_scale", "title": "Rate it"}
        )
        scale = out["questionItem"]["question"]["scaleQuestion"]
        assert scale == {"low": 1, "high": 5, "lowLabel": "", "highLabel": ""}

    def test_linear_scale_invalid_range(self):
        with pytest.raises(ValidationError, match="low<high"):
            forms_lib._item_spec_to_api(
                {"type": "linear_scale", "title": "x", "low": 5, "high": 5}
            )

    def test_linear_scale_non_int(self):
        with pytest.raises(ValidationError, match="low<high"):
            forms_lib._item_spec_to_api(
                {"type": "linear_scale", "title": "x", "low": "1", "high": 5}
            )

    def test_radio(self):
        out = forms_lib._item_spec_to_api(
            {"type": "radio", "title": "Role?", "options": ["A", "B", "C"]}
        )
        choice = out["questionItem"]["question"]["choiceQuestion"]
        assert choice["type"] == "RADIO"
        assert choice["options"] == [{"value": "A"}, {"value": "B"}, {"value": "C"}]
        assert choice["shuffle"] is False

    def test_checkbox(self):
        out = forms_lib._item_spec_to_api(
            {"type": "checkbox", "title": "Pick all", "options": ["A", "B"]}
        )
        assert out["questionItem"]["question"]["choiceQuestion"]["type"] == "CHECKBOX"

    def test_dropdown(self):
        out = forms_lib._item_spec_to_api(
            {"type": "dropdown", "title": "Pick", "options": ["A"]}
        )
        assert (
            out["questionItem"]["question"]["choiceQuestion"]["type"] == "DROP_DOWN"
        )

    def test_radio_with_include_other(self):
        out = forms_lib._item_spec_to_api(
            {
                "type": "radio",
                "title": "Role?",
                "options": ["A", "B"],
                "include_other": True,
            }
        )
        choice = out["questionItem"]["question"]["choiceQuestion"]
        assert choice["options"] == [
            {"value": "A"},
            {"value": "B"},
            {"isOther": True},
        ]

    def test_checkbox_with_include_other(self):
        out = forms_lib._item_spec_to_api(
            {
                "type": "checkbox",
                "title": "Pick all",
                "options": ["A", "B"],
                "include_other": True,
            }
        )
        opts = out["questionItem"]["question"]["choiceQuestion"]["options"]
        assert opts[-1] == {"isOther": True}

    def test_dropdown_rejects_include_other(self):
        with pytest.raises(ValidationError, match="dropdown"):
            forms_lib._item_spec_to_api(
                {
                    "type": "dropdown",
                    "title": "Pick",
                    "options": ["A", "B"],
                    "include_other": True,
                }
            )

    def test_radio_shuffle(self):
        out = forms_lib._item_spec_to_api(
            {
                "type": "radio",
                "title": "x",
                "options": ["A", "B"],
                "shuffle": True,
            }
        )
        assert out["questionItem"]["question"]["choiceQuestion"]["shuffle"] is True

    def test_radio_options_required(self):
        with pytest.raises(ValidationError, match="options"):
            forms_lib._item_spec_to_api({"type": "radio", "title": "x"})

    def test_radio_options_must_be_list(self):
        with pytest.raises(ValidationError, match="options"):
            forms_lib._item_spec_to_api(
                {"type": "radio", "title": "x", "options": "A,B"}
            )

    def test_date_default(self):
        out = forms_lib._item_spec_to_api({"type": "date", "title": "When"})
        date_q = out["questionItem"]["question"]["dateQuestion"]
        assert date_q == {"includeTime": False, "includeYear": True}

    def test_date_with_time(self):
        out = forms_lib._item_spec_to_api(
            {
                "type": "date",
                "title": "When",
                "include_time": True,
                "include_year": False,
            }
        )
        date_q = out["questionItem"]["question"]["dateQuestion"]
        assert date_q == {"includeTime": True, "includeYear": False}

    def test_question_description(self):
        out = forms_lib._item_spec_to_api(
            {
                "type": "text",
                "title": "Name",
                "description": "First and last",
            }
        )
        assert out["description"] == "First and last"

    def test_unknown_type_rejected(self):
        with pytest.raises(ValidationError, match="Unknown item type"):
            forms_lib._item_spec_to_api({"type": "rating", "title": "x"})

    def test_missing_type_rejected(self):
        with pytest.raises(ValidationError, match="missing 'type'"):
            forms_lib._item_spec_to_api({"title": "x"})

    def test_missing_title_for_question_rejected(self):
        with pytest.raises(ValidationError, match="requires non-empty 'title'"):
            forms_lib._item_spec_to_api({"type": "text"})

    def test_empty_title_for_question_rejected(self):
        with pytest.raises(ValidationError, match="requires non-empty 'title'"):
            forms_lib._item_spec_to_api({"type": "text", "title": ""})

    def test_section_allows_empty_title(self):
        # A page break with no title is awkward but not invalid.
        out = forms_lib._item_spec_to_api({"type": "section"})
        assert out == {"title": "", "pageBreakItem": {}}

    def test_non_dict_rejected(self):
        with pytest.raises(ValidationError, match="must be a JSON object"):
            forms_lib._item_spec_to_api(["type", "text"])
